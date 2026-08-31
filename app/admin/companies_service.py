from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.accounts.models import AsaasSubaccountLink
from app.accounts.repository import AsaasSubaccountLinkRepository
from app.admin.payments_service import AdminPaymentsService
from app.admin.repository import AdminRepository
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.integrations.relay_service import SelolibertariaRelayService


class AdminCompaniesService:
    """Backs the admin "Empresas" screen (docs/plano-admin-empresas-cota-split.md
    §5/§6): companies, their linked Asaas payment method, and the split
    quota configured for that link. Delegates any live Asaas call
    (subaccount creation, split-rule sums) to AdminPaymentsService rather
    than duplicating its AsaasClient wiring or quota-check logic.

    `relay_service` is optional so this service still works wherever it's
    constructed without the Selolibertaria integration wired up (e.g. an
    existing caller/test that only cares about split-quota management); when
    present, provisioning a *new* subaccount (docs/prompts/
    asaas-payment-integration/03-prompt-devbutter-relay-e-rotacao.md,
    entregavel 2) also issues an access token and relays it.
    """

    def __init__(
        self,
        repository: AdminRepository,
        link_repository: AsaasSubaccountLinkRepository,
        payments_service: AdminPaymentsService,
        relay_service: SelolibertariaRelayService | None = None,
    ):
        self._repository = repository
        self._link_repository = link_repository
        self._payments_service = payments_service
        self._relay_service = relay_service

    async def list_companies(
        self, *, search: str | None, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        rows, total = await self._repository.list_companies(
            search=search, offset=offset, limit=limit
        )
        items = []
        for row in rows:
            link: AsaasSubaccountLink | None = row["link"]
            used_percent = await self._active_percentage(link) if link else None
            items.append(
                {
                    "company": row["company"],
                    "user": row["user"],
                    "link": link,
                    "used_percent": used_percent,
                }
            )
        return items, total

    async def get_company(self, company_id: uuid.UUID) -> dict[str, Any]:
        row = await self._repository.get_company_detail(company_id)
        if row is None:
            raise NotFoundError("Company not found")
        link: AsaasSubaccountLink | None = row["link"]
        split_rules: list[dict[str, Any]] = []
        access_tokens: dict[str, Any] = {}
        used_percent = None
        if link is not None:
            split_rules = await self._payments_service.list_split_rules(link.asaas_account_id)
            access_tokens = await self._payments_service.list_access_tokens(
                link.asaas_account_id
            )
            used_percent = self._sum_active_percentage(split_rules)
        return {
            "company": row["company"],
            "user": row["user"],
            "link": link,
            "split_rules": split_rules,
            "access_tokens": access_tokens,
            "used_percent": used_percent,
        }

    async def set_split_quota(
        self, company_id: uuid.UUID, split_quota_percent: Decimal | None, admin_user_id: uuid.UUID
    ) -> AsaasSubaccountLink:
        link = await self._link_repository.get_by_company(company_id)
        if link is None:
            raise NotFoundError(
                "This company has no payment method configured yet -- link a subaccount first"
            )
        link.split_quota_percent = split_quota_percent
        link.quota_set_by = admin_user_id
        link.quota_updated_at = datetime.now(UTC)
        return await self._link_repository.update(link)

    async def link_subaccount(
        self, company_id: uuid.UUID, payload: dict[str, Any]
    ) -> AsaasSubaccountLink:
        existing = await self._link_repository.get_by_company(company_id)
        if existing is not None:
            raise ValidationFailedError("This company already has a payment method configured")

        asaas_account_id = payload.get("asaas_account_id")
        is_new_subaccount = not asaas_account_id
        if asaas_account_id:
            # Linking an Asaas subaccount that already exists (created out
            # of band, or previously via /admin/subaccounts). No credential
            # to relay here -- whoever created it out of band is responsible
            # for its own key material.
            subaccount = await self._payments_service.get_subaccount(asaas_account_id)
            wallet_id = subaccount.get("walletId")
        else:
            # No existing subaccount referenced -- provision a new one.
            subaccount = await self._payments_service.create_subaccount(payload)
            asaas_account_id = subaccount["id"]
            wallet_id = subaccount.get("walletId")

        link = AsaasSubaccountLink(
            company_id=company_id,
            asaas_account_id=asaas_account_id,
            wallet_id=wallet_id,
        )
        link = await self._link_repository.create(link)

        if is_new_subaccount and self._relay_service is not None:
            # Entregavel 2: relay the initial credential to Selolibertaria.
            # Asaas never returns a subaccount's own apiKey again after
            # creation (00-levantamento-tecnico.md item B2) -- a dedicated
            # access token is what's actually relayable, same as the
            # rotation path (app/integrations/rotation_service.py).
            access_token_response = await self._payments_service.create_access_token(
                asaas_account_id, {"name": "selolibertaria-relay-initial"}
            )
            link = await self._relay_service.relay_initial_provisioning(
                link, access_token_response
            )

        return link

    async def _active_percentage(self, link: AsaasSubaccountLink) -> Decimal:
        rules = await self._payments_service.list_split_rules(link.asaas_account_id)
        return self._sum_active_percentage(rules)

    @staticmethod
    def _sum_active_percentage(rules: list[dict[str, Any]]) -> Decimal:
        return sum(
            (
                Decimal(str(rule["value"]))
                for rule in rules
                if rule.get("active") and rule.get("type") == "PERCENTAGE"
            ),
            Decimal("0"),
        )
