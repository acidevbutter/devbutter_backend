from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.accounts.repository import AsaasSubaccountLinkRepository
from app.core.clients.asaas_client import AsaasClient
from app.core.exceptions import ValidationFailedError


class AdminPaymentsService:
    """Thin admin-facing wrapper around AsaasClient's subaccount/token/
    split/payment/audit methods (docs/plano-admin-asaas-integracao.md
    §2.4). The only behavior beyond passthrough is the split-quota check
    (docs/plano-admin-empresas-cota-split.md §4): before a PERCENTAGE split
    rule is created or its value changed, this confirms the company's
    AsaasSubaccountLink.split_quota_percent (if any) isn't exceeded --
    rejecting here, in the admin's own vocabulary, saves a round trip to
    asaas_payment, which still enforces its own fixed 100% ceiling as a
    second, independent layer of defense.
    """

    def __init__(self, asaas_client: AsaasClient, link_repository: AsaasSubaccountLinkRepository):
        self._asaas_client = asaas_client
        self._link_repository = link_repository

    # -- Subaccounts --------------------------------------------------

    async def list_subaccounts(self, filters: dict[str, Any]) -> dict[str, Any]:
        return await self._asaas_client.list_subaccounts(filters)

    async def get_subaccount(self, account_id: str) -> dict[str, Any]:
        return await self._asaas_client.get_subaccount(account_id)

    async def create_subaccount(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._asaas_client.create_subaccount(payload)

    async def close_subaccount(self, account_id: str) -> dict[str, Any]:
        return await self._asaas_client.close_subaccount(account_id)

    async def resend_activation_link(self, account_id: str) -> dict[str, Any]:
        return await self._asaas_client.resend_activation_link(account_id)

    # -- Access tokens --------------------------------------------------

    async def list_access_tokens(self, account_id: str) -> dict[str, Any]:
        return await self._asaas_client.list_access_tokens(account_id)

    async def create_access_token(self, account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._asaas_client.create_access_token(account_id, payload)

    async def revoke_access_token(self, account_id: str, token_id: str) -> dict[str, Any]:
        return await self._asaas_client.revoke_access_token(account_id, token_id)

    # -- Split rules --------------------------------------------------

    async def list_split_rules(self, account_id: str) -> list[dict[str, Any]]:
        return await self._asaas_client.list_split_rules(account_id)

    async def create_split_rule(self, account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("type") == "PERCENTAGE":
            await self._assert_within_quota(account_id, Decimal(str(payload["value"])))
        return await self._asaas_client.create_split_rule(account_id, payload)

    async def update_split_rule(
        self, account_id: str, rule_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if payload.get("value") is not None:
            rules = await self._asaas_client.list_split_rules(account_id)
            rule = next((r for r in rules if str(r.get("id")) == str(rule_id)), None)
            if rule is not None and rule.get("type") == "PERCENTAGE":
                await self._assert_within_quota(
                    account_id, Decimal(str(payload["value"])), exclude_rule_id=rule_id
                )
        return await self._asaas_client.update_split_rule(account_id, rule_id, payload)

    async def _assert_within_quota(
        self, account_id: str, new_value: Decimal, *, exclude_rule_id: str | None = None
    ) -> None:
        link = await self._link_repository.get_by_asaas_account_id(account_id)
        if link is None or link.split_quota_percent is None:
            return
        rules = await self._asaas_client.list_split_rules(account_id)
        current_sum = sum(
            (
                Decimal(str(rule["value"]))
                for rule in rules
                if rule.get("active")
                and rule.get("type") == "PERCENTAGE"
                and str(rule.get("id")) != str(exclude_rule_id)
            ),
            Decimal("0"),
        )
        if current_sum + new_value > link.split_quota_percent:
            raise ValidationFailedError(
                "excede a cota de split definida para esta empresa "
                f"({link.split_quota_percent}%)"
            )

    # -- Payments --------------------------------------------------

    async def list_payments(self, account_id: str, filters: dict[str, Any]) -> dict[str, Any]:
        return await self._asaas_client.list_payments(account_id, filters)

    async def get_payment(self, account_id: str, payment_id: str) -> dict[str, Any]:
        return await self._asaas_client.get_payment(account_id, payment_id)

    async def refund_payment(
        self, account_id: str, payment_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._asaas_client.refund_payment(account_id, payment_id, payload)

    async def cancel_payment(self, account_id: str, payment_id: str) -> dict[str, Any]:
        return await self._asaas_client.cancel_payment(account_id, payment_id)

    # -- Audit --------------------------------------------------

    async def get_audit_logs(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._asaas_client.get_audit_logs(filters)
