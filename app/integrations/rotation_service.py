"""Proactive (scheduled) and reactive (endpoint-triggered) credential
rotation -- docs/prompts/asaas-payment-integration/
03-prompt-devbutter-relay-e-rotacao.md, entregaveis 3 and 4.

Both paths converge on `rotate()`, which asks asaas_client.py for a new
access token via the existing mTLS proxy path (same route AdminPaymentsService
uses) and hands the result to SelolibertariaRelayService. A rotation call
that fails upstream (UpstreamServiceError) is treated as an observable alert
state (`rotation_blocked`), not a bug -- see 00-levantamento-tecnico.md item
D.1: managing a subaccount's access tokens from the root account requires a
manual 2-hour window enabled in the Asaas dashboard with IP allowlisting, so
a 403 here can simply mean that window is currently closed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from app.accounts.models import AsaasSubaccountLink
from app.accounts.repository import AsaasSubaccountLinkRepository
from app.core.clients.asaas_client import AsaasClient
from app.core.exceptions import UpstreamServiceError
from app.integrations.relay_service import SelolibertariaRelayService

RotationReason = Literal["proactive", "reactive"]

# ACCOUNT_STATUS_* events that should trigger a proactive rotation -- see
# 00-levantamento-tecnico.md item B5/D.7. Confirmed event names only; no
# other ACCOUNT_STATUS_* variant is treated as a rotation trigger.
ACCOUNT_STATUS_ROTATION_TRIGGERS = frozenset(
    {
        "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON",
        "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRED",
    }
)

# How far ahead of AccessToken.expiration_date the proactive sweep rotates.
# Not specified by any prompt/ADR/levantamento -- a documented default,
# not an Asaas-confirmed behavior.
DEFAULT_EXPIRING_SOON_WINDOW = timedelta(days=7)


class CredentialRotationService:
    def __init__(
        self,
        asaas_client: AsaasClient,
        link_repository: AsaasSubaccountLinkRepository,
        relay_service: SelolibertariaRelayService,
        *,
        expiring_soon_window: timedelta = DEFAULT_EXPIRING_SOON_WINDOW,
    ):
        self._asaas_client = asaas_client
        self._link_repository = link_repository
        self._relay_service = relay_service
        self._expiring_soon_window = expiring_soon_window

    async def run_proactive_sweep(self) -> list[AsaasSubaccountLink]:
        """Entry point for the scheduled job (scripts/run_proactive_rotation.py).
        Checks every linked subaccount's active access token expiration and
        rotates the ones due. Returns every link this run touched (rotated,
        or newly marked rotation_blocked) so the caller can log/alert.
        """
        touched: list[AsaasSubaccountLink] = []
        for link in await self._link_repository.list_with_access_token():
            if await self._is_due_for_rotation(link):
                touched.append(await self.rotate(link, reason="proactive"))
        return touched

    async def handle_account_status_event(
        self, event_type: str, asaas_account_id: str | None
    ) -> AsaasSubaccountLink | None:
        """Called from app/webhooks/service.py when an ACCOUNT_STATUS_*
        webhook arrives from Asaas. Returns None (no-op) for any event type
        outside ACCOUNT_STATUS_ROTATION_TRIGGERS, or when no link is found
        for the account -- both are legitimate outcomes, not errors.
        """
        if event_type not in ACCOUNT_STATUS_ROTATION_TRIGGERS or not asaas_account_id:
            return None
        link = await self._link_repository.get_by_asaas_account_id(asaas_account_id)
        if link is None:
            return None
        return await self.rotate(link, reason="proactive")

    async def rotate(
        self, link: AsaasSubaccountLink, *, reason: RotationReason
    ) -> AsaasSubaccountLink:
        token_name = f"selolibertaria-relay-{reason}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        try:
            response = await self._asaas_client.create_access_token(
                link.asaas_account_id, {"name": token_name}
            )
        except UpstreamServiceError as exc:
            link.selolibertaria_relay_status = "rotation_blocked"
            link.selolibertaria_relay_attempted_at = datetime.now(UTC)
            link.selolibertaria_relay_last_error = (
                f"Asaas token rotation failed ({reason}): {exc.detail}"
            )
            return await self._link_repository.update(link)
        return await self._relay_service.relay_key_rotation(link, response, reason=reason)

    async def _is_due_for_rotation(self, link: AsaasSubaccountLink) -> bool:
        if not link.access_token_id:
            return False
        tokens = await self._asaas_client.list_access_tokens(link.asaas_account_id)
        entries = tokens.get("data") if isinstance(tokens, dict) else tokens
        if not isinstance(entries, list):
            return False
        token = next(
            (entry for entry in entries if str(entry.get("id")) == str(link.access_token_id)),
            None,
        )
        if token is None:
            return False
        expiration = token.get("expirationDate")
        if not expiration:
            return False
        try:
            expires_at = datetime.fromisoformat(str(expiration))
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at - datetime.now(UTC) <= self._expiring_soon_window
