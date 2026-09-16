"""Creates a Company's Asaas white-label subaccount (Layer B) from its
``CompanyBillingProfile`` and records the non-sensitive reference in
``AsaasSubaccountLink``. The subaccount's credential never touches this repo --
asaas_payment keeps it encrypted; here we only ever see ``id`` and ``walletId``.

Two entry points, both idempotent:
  * ``provision`` -- ``WebhookService`` calls this automatically, when a
    Company's first Layer A invoice is paid (docs/architecture/adr-0002
    revised trigger), building the Asaas body from the billing profile;
  * ``create_manual`` -- the admin "Empresas" screen, for when that profile
    is missing/incomplete (``provision`` can't run) or a previous attempt
    left the link ``CREATION_FAILED``: the admin supplies the body by hand.

Both funnel into ``_create``, so a failure is swallowed the same way either
way (recorded as ``CREATION_FAILED`` + ``last_error``) and never breaks the
webhook or admin request that triggered it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.accounts.models import AsaasSubaccountLink, Company, CompanyBillingProfile, User
from app.accounts.repository import (
    AccountRepository,
    AsaasSubaccountLinkRepository,
    CompanyBillingProfileRepository,
)
from app.core.clients.asaas_client import AsaasClient
from app.core.exceptions import UpstreamServiceError, ValidationFailedError
from app.core.metrics import emit_metrics
from app.notifications.service import NotificationService
from app.settings.config import settings

logger = logging.getLogger(__name__)

# Every account-situation event Asaas can emit for a subaccount
# (asaas_subcontas_fluxo_completo.md §11). Registered on the subaccount at
# creation so onboarding/approval is tracked by webhook, not polling.
ACCOUNT_STATUS_EVENTS = [
    "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_APPROVED",
    "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_AWAITING_APPROVAL",
    "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_PENDING",
    "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_REJECTED",
    "ACCOUNT_STATUS_COMMERCIAL_INFO_APPROVED",
    "ACCOUNT_STATUS_COMMERCIAL_INFO_AWAITING_APPROVAL",
    "ACCOUNT_STATUS_COMMERCIAL_INFO_PENDING",
    "ACCOUNT_STATUS_COMMERCIAL_INFO_REJECTED",
    "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON",
    "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRED",
    "ACCOUNT_STATUS_DOCUMENT_APPROVED",
    "ACCOUNT_STATUS_DOCUMENT_AWAITING_APPROVAL",
    "ACCOUNT_STATUS_DOCUMENT_PENDING",
    "ACCOUNT_STATUS_DOCUMENT_REJECTED",
    "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED",
    "ACCOUNT_STATUS_GENERAL_APPROVAL_AWAITING_APPROVAL",
    "ACCOUNT_STATUS_GENERAL_APPROVAL_PENDING",
    "ACCOUNT_STATUS_GENERAL_APPROVAL_REJECTED",
]

_TERMINAL_STATUSES = {"PROVISIONED", "CLOSED"}


class SubaccountProvisioningResult:
    __slots__ = ("link", "created", "detail")

    def __init__(self, link: AsaasSubaccountLink, *, created: bool, detail: str) -> None:
        self.link = link
        self.created = created
        self.detail = detail


class SubaccountProvisioningService:
    def __init__(
        self,
        asaas_client: AsaasClient,
        link_repository: AsaasSubaccountLinkRepository,
        billing_profile_repository: CompanyBillingProfileRepository,
        account_repository: AccountRepository,
        notification_service: NotificationService,
    ) -> None:
        self._asaas_client = asaas_client
        self._link_repository = link_repository
        self._billing_profile_repository = billing_profile_repository
        self._account_repository = account_repository
        self._notification_service = notification_service

    async def provision(
        self, company_id: uuid.UUID, *, actor: str
    ) -> SubaccountProvisioningResult:
        started = await self._start(company_id)
        if isinstance(started, SubaccountProvisioningResult):
            return started
        company, link, owner = started

        profile = await self._billing_profile_repository.get_by_company(company_id)
        if profile is None:
            raise ValidationFailedError(
                "Company has no billing profile -- collect os dados de recebimento first"
            )
        webhook_email = settings.asaas_webhook_ops_email or (
            owner.email if owner is not None else profile.billing_email
        )
        body = self._build_body(profile, webhook_email)
        return await self._create(company, link, body, actor=actor, owner=owner)

    async def create_manual(
        self, company_id: uuid.UUID, payload: dict[str, Any], *, actor: str
    ) -> SubaccountProvisioningResult:
        """Same success/failure bookkeeping as ``provision``, but the Asaas
        body is whatever the admin typed in by hand instead of being built
        from a ``CompanyBillingProfile`` -- the fallback for when that
        profile is missing/incomplete (so ``provision`` can't run) or a
        previous automatic attempt left the link ``CREATION_FAILED``. Still
        attaches the same account-status webhook, so onboarding/approval
        tracking works the same regardless of how the subaccount was
        created.
        """
        started = await self._start(company_id)
        if isinstance(started, SubaccountProvisioningResult):
            return started
        company, link, owner = started

        webhook_email = settings.asaas_webhook_ops_email or (
            owner.email if owner is not None else payload.get("email")
        )
        body = {**payload, "webhooks": self._status_webhooks(webhook_email)}
        return await self._create(company, link, body, actor=actor, owner=owner)

    async def _start(
        self, company_id: uuid.UUID
    ) -> tuple[Company, AsaasSubaccountLink | None, User | None] | SubaccountProvisioningResult:
        """Shared preamble of both entry points: look up the existing link
        (short-circuiting if it's already terminal) and the company/owner
        every body-building path needs regardless of where the Asaas fields
        come from."""
        link = await self._link_repository.get_by_company(company_id)
        if link is not None and link.provisioning_status in _TERMINAL_STATUSES:
            return SubaccountProvisioningResult(link, created=False, detail="already_provisioned")

        company = await self._account_repository.get_company(company_id)
        if company is None:
            raise ValidationFailedError(f"Company {company_id} not found")
        owner = await self._account_repository.get_user(company.user_id)
        return company, link, owner

    async def _create(
        self,
        company: Company,
        link: AsaasSubaccountLink | None,
        body: dict[str, Any],
        *,
        actor: str,
        owner: User | None,
    ) -> SubaccountProvisioningResult:
        if not settings.backend_public_base_url:
            raise ValidationFailedError(
                "BACKEND_PUBLIC_BASE_URL is not configured -- refusing to create a "
                "subaccount without a status webhook"
            )
        link = link or await self._link_repository.get_or_create(company.id)
        try:
            created = await self._asaas_client.create_subaccount(body)
        except UpstreamServiceError as exc:
            link.provisioning_status = "CREATION_FAILED"
            link.last_error = str(exc)[:1000]
            await self._link_repository.update(link)
            await emit_metrics(
                {"Environment": settings.environment, "Actor": actor.split(":")[0]},
                [("SubaccountProvisioningFailed", 1, "Count")],
            )
            logger.warning("subaccount provisioning failed for company %s: %s", company.id, exc)
            return SubaccountProvisioningResult(link, created=False, detail="creation_failed")

        link.asaas_account_id = created.get("id")
        link.wallet_id = created.get("walletId")
        link.provisioning_status = "PROVISIONED"
        link.last_error = None
        link.onboarding_status = "AWAITING_ONBOARDING"
        link.approval_status = "PENDING"
        await self._link_repository.update(link)

        await emit_metrics(
            {"Environment": settings.environment, "Actor": actor.split(":")[0]},
            [("SubaccountProvisioned", 1, "Count")],
        )
        if owner is not None:
            try:
                self._notification_service.notify_subaccount_onboarding(owner, company.name)
            except Exception:  # pragma: no cover - delivery is best effort
                logger.exception("subaccount onboarding notification could not be queued")
        return SubaccountProvisioningResult(link, created=True, detail="provisioned")

    @staticmethod
    def _status_webhooks(notify_email: str | None) -> list[dict[str, Any]]:
        return [
            {
                "name": "devbutter-account-status",
                "url": f"{settings.backend_public_base_url}/webhooks/asaas",
                "email": notify_email,
                "enabled": True,
                "interrupted": False,
                "apiVersion": 3,
                "authToken": settings.asaas_webhook_access_token,
                "sendType": "SEQUENTIALLY",
                "events": ACCOUNT_STATUS_EVENTS,
            }
        ]

    def _build_body(
        self, profile: CompanyBillingProfile, webhook_email: str | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": profile.legal_name,
            "email": profile.billing_email,
            "cpfCnpj": profile.cnpj,
            "companyType": profile.company_type,
            "mobilePhone": profile.mobile_phone,
            "incomeValue": float(profile.income_value),
            "address": profile.address,
            "addressNumber": profile.address_number,
            "province": profile.province,
            "postalCode": profile.postal_code,
            "webhooks": self._status_webhooks(webhook_email),
        }
        # Both optional, and asaas_payment's `exclude_none=True` only drops an
        # actual None -- a "" saved from a portal form that never distinguishes
        # blank from absent would still reach Asaas. `site` in particular
        # fails Asaas's URL validation on an empty string (never on a missing
        # field), so omit both outright rather than send them empty.
        if profile.complement:
            body["complement"] = profile.complement
        if profile.site:
            body["site"] = profile.site
        return body

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)
