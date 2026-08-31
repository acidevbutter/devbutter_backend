"""Owns AsaasSubaccountLink.selolibertaria_relay_* bookkeeping around
SelolibertariaClient calls -- docs/prompts/asaas-payment-integration/
03-prompt-devbutter-relay-e-rotacao.md, entregaveis 2 and 3. Never persists
`api_key` anywhere -- it only ever lives in the one outbound HTTP call this
service makes per relay/rotation, plus whatever asaas_client.py's caller
already held it in for that single request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from app.accounts.models import AsaasSubaccountLink
from app.accounts.repository import AsaasSubaccountLinkRepository
from app.integrations.selolibertaria_client import SelolibertariaClient

RotationReason = Literal["proactive", "reactive"]

# asaas_payment currently redacts `apiKey` from every /subaccounts response,
# including POST .../access-tokens (hide_api_key=True in
# asaas_payment/app/subaccounts/router.py) -- so devbutter_backend cannot
# actually obtain a relayable credential through AsaasClient today. This is
# a real gap discovered while implementing this slice (out of scope to fix
# here: asaas_payment is a different service/repo, not in this slice's read
# scope). Rather than relay a missing/None api_key, every path below treats
# a missing apiKey as an observable "blocked" state -- see the execution log
# appended to 03-prompt-devbutter-relay-e-rotacao.md for the full note.
_MISSING_API_KEY_PROVISIONING = (
    "asaas_payment did not return an apiKey for this access token "
    "(hide_api_key redaction on /subaccounts/{id}/access-tokens) -- cannot "
    "relay a credential to Selolibertaria until that proxy route is changed "
    "to reveal it to devbutter_backend"
)
_MISSING_API_KEY_ROTATION = (
    "asaas_payment did not return an apiKey for the rotated access token "
    "(hide_api_key redaction) -- cannot relay the new credential"
)


class SelolibertariaRelayService:
    def __init__(
        self,
        relay_client: SelolibertariaClient,
        link_repository: AsaasSubaccountLinkRepository,
    ):
        self._relay_client = relay_client
        self._link_repository = link_repository

    async def relay_initial_provisioning(
        self, link: AsaasSubaccountLink, access_token_response: dict[str, Any]
    ) -> AsaasSubaccountLink:
        api_key = access_token_response.get("apiKey")
        access_token_id = access_token_response.get("id")
        if not api_key:
            return await self._mark_blocked(link, _MISSING_API_KEY_PROVISIONING)

        payload = {
            "event_id": str(uuid.uuid4()),
            "devbutter_company_id": str(link.company_id),
            "asaas_account_id": link.asaas_account_id,
            "wallet_id": link.wallet_id,
            "access_token_ref": access_token_id,
            "api_key": api_key,
            "issued_at": datetime.now(UTC).isoformat(),
        }
        result = await self._relay_client.send_subaccount_provisioned(payload)
        link.access_token_id = access_token_id
        link.key_version = 1
        return await self._apply_result(link, result)

    async def relay_key_rotation(
        self,
        link: AsaasSubaccountLink,
        access_token_response: dict[str, Any],
        *,
        reason: RotationReason,
    ) -> AsaasSubaccountLink:
        api_key = access_token_response.get("apiKey")
        access_token_id = access_token_response.get("id")
        if not api_key:
            return await self._mark_blocked(link, _MISSING_API_KEY_ROTATION)

        next_version = (link.key_version or 0) + 1
        payload = {
            "event_id": str(uuid.uuid4()),
            "asaas_account_id": link.asaas_account_id,
            "api_key": api_key,
            "key_version": next_version,
            "reason": reason,
        }
        result = await self._relay_client.send_key_updated(link.asaas_account_id, payload)
        link.access_token_id = access_token_id
        link.key_version = next_version
        return await self._apply_result(link, result)

    async def _apply_result(
        self, link: AsaasSubaccountLink, result: Any
    ) -> AsaasSubaccountLink:
        link.selolibertaria_relay_status = result.status
        link.selolibertaria_relay_attempted_at = datetime.now(UTC)
        link.selolibertaria_relay_last_error = result.detail if result.status == "failed" else None
        return await self._link_repository.update(link)

    async def _mark_blocked(
        self, link: AsaasSubaccountLink, message: str
    ) -> AsaasSubaccountLink:
        link.selolibertaria_relay_status = "blocked"
        link.selolibertaria_relay_attempted_at = datetime.now(UTC)
        link.selolibertaria_relay_last_error = message
        return await self._link_repository.update(link)
