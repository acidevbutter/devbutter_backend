"""Creates a Company's Asaas white-label subaccount (Layer B) from its
``CompanyBillingProfile`` and records the non-sensitive reference in
``AsaasSubaccountLink``. The subaccount's credential never touches this repo --
asaas_payment keeps it encrypted; here we only ever see ``id`` and ``walletId``.

Two callers, one method (``provision``), idempotent:
  * ``WebhookService`` -- automatically, when a Company's first Layer A invoice
    is paid (docs/architecture/adr-0002 revised trigger);
  * the admin "Empresas" screen -- manually, incl. retry after a failure.

A failure is swallowed (recorded as ``CREATION_FAILED`` + ``last_error``) so it
never breaks the webhook that triggered it or the payment reconciliation.
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
        link = await self._link_repository.get_by_company(company_id)
        if link is not None and link.provisioning_status in _TERMINAL_STATUSES:
            return SubaccountProvisioningResult(
                link, created=False, detail="already_provisioned"
            )

        company = await self._account_repository.get_company(company_id)
        if company is None:
            raise ValidationFailedError(f"Company {company_id} not found")
        profile = await self._billing_profile_repository.get_by_company(company_id)
        if profile is None:
            raise ValidationFailedError(
                "Company has no billing profile -- collect os dados de recebimento first"
            )
        if not settings.backend_public_base_url:
            raise ValidationFailedError(
                "BACKEND_PUBLIC_BASE_URL is not configured -- refusing to create a "
                "subaccount without a status webhook"
            )
        owner = await self._account_repository.get_user(company.user_id)

        link = link or await self._link_repository.get_or_create(company_id)
        body = self._build_body(company, profile, owner)
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
            logger.warning("subaccount provisioning failed for company %s: %s", company_id, exc)
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

    def _build_body(
        self, company: Company, profile: CompanyBillingProfile, owner: User | None
    ) -> dict[str, Any]:
        webhook_email = settings.asaas_webhook_ops_email or (
            owner.email if owner is not None else profile.billing_email
        )
        return {
            "name": profile.legal_name,
            "email": profile.billing_email,
            "cpfCnpj": profile.cnpj,
            "companyType": profile.company_type,
            "mobilePhone": profile.mobile_phone,
            "incomeValue": float(profile.income_value),
            "address": profile.address,
            "addressNumber": profile.address_number,
            "complement": profile.complement,
            "province": profile.province,
            "postalCode": profile.postal_code,
            "site": profile.site,
            "webhooks": [
                {
                    "name": "devbutter-account-status",
                    "url": f"{settings.backend_public_base_url}/webhooks/asaas",
                    "email": webhook_email,
                    "enabled": True,
                    "interrupted": False,
                    "apiVersion": 3,
                    "authToken": settings.asaas_webhook_access_token,
                    "sendType": "SEQUENTIALLY",
                    "events": ACCOUNT_STATUS_EVENTS,
                }
            ],
        }

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)
