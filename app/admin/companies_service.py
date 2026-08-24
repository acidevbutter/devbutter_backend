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


class AdminCompaniesService:
    """Backs the admin "Empresas" screen (docs/plano-admin-empresas-cota-split.md
    §5/§6): companies, their linked Asaas payment method, and the split
    quota configured for that link. Delegates any live Asaas call
    (subaccount creation, split-rule sums) to AdminPaymentsService rather
    than duplicating its AsaasClient wiring or quota-check logic.
    """

    def __init__(
        self,
        repository: AdminRepository,
        link_repository: AsaasSubaccountLinkRepository,
        payments_service: AdminPaymentsService,
    ):
        self._repository = repository
        self._link_repository = link_repository
        self._payments_service = payments_service

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
        if asaas_account_id:
            # Linking an Asaas subaccount that already exists (created out
            # of band, or previously via /admin/subaccounts).
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
        return await self._link_repository.create(link)

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
