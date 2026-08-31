"""Outbound server-to-server client to selolibertaria/backend for the
credential relay + rotation flow (ADR-0001, Option B --
docs/prompts/asaas-payment-integration/01-adr-integracao-asaas.md). Signs
every request per CONTRATO-HMAC.md and never logs a request/response body,
since the body of both routes below carries `api_key` in clear text --
@log_call (app/core/decorators.py) only ever logs the qualified method name,
never arguments, so it stays safe to use here.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.decorators import log_call
from app.integrations.hmac_signing import sign
from app.settings.config import settings

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5
_RETRYABLE_STATUS_FLOOR = 500

RelayStatus = Literal["sent", "acked", "failed"]


@dataclass(frozen=True)
class RelayResult:
    """`sent` = delivered, receiver accepted for async processing (202).
    `acked` = receiver confirmed the credential was applied (200) -- see
    the sequence diagram in diagrams/fluxo-novo-provisionamento-e-rotacao.mmd,
    both the initial relay's "202 Accepted (ack de recebimento)" and a
    rotation's "200 ack (confirmacao de aplicacao)". `failed` = exhausted
    retries or got a non-2xx/5xx response; `detail` is a short diagnostic
    string, never the request/response body (would contain api_key).
    """

    status: RelayStatus
    status_code: int | None
    detail: str | None = None


class SelolibertariaClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.selolibertaria_backend_base_url,
            timeout=settings.selolibertaria_relay_request_timeout_seconds,
        )

    @log_call
    async def send_subaccount_provisioned(self, payload: dict[str, Any]) -> RelayResult:
        """POST /integrations/devbutter/subaccounts -- initial relay. Body
        per CONTRATO-HMAC.md / 02-system-design-payment-service.md §4:
        {event_id, devbutter_company_id, asaas_account_id, wallet_id,
        access_token_ref, api_key, issued_at}.
        """
        return await self._post_signed("/integrations/devbutter/subaccounts", payload)

    @log_call
    async def send_key_updated(
        self, asaas_account_id: str, payload: dict[str, Any]
    ) -> RelayResult:
        """POST /integrations/devbutter/subaccounts/{id}/key-updated -- push
        of a rotated credential. Body: {event_id, api_key, key_version, reason}.
        """
        return await self._post_signed(
            f"/integrations/devbutter/subaccounts/{asaas_account_id}/key-updated", payload
        )

    async def _post_signed(self, path: str, payload: dict[str, Any]) -> RelayResult:
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        secret = settings.devbutter_selolibertaria_relay_hmac_secret
        last_detail: str | None = None
        last_status_code: int | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            signed = sign(secret, raw_body)
            try:
                response = await self._client.post(
                    path,
                    content=raw_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-DevButter-Signature": signed.signature,
                        "X-DevButter-Timestamp": signed.timestamp,
                        "X-DevButter-Nonce": signed.nonce,
                    },
                )
            except httpx.HTTPError as exc:
                # Never str(exc) here -- httpx exceptions can echo the
                # request URL/body on some error types. Only the exception
                # class name is diagnostic-safe.
                last_detail = f"transport error: {exc.__class__.__name__}"
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
                    continue
                return RelayResult(status="failed", status_code=None, detail=last_detail)

            last_status_code = response.status_code
            if response.status_code == 200:
                return RelayResult(status="acked", status_code=200)
            if response.status_code == 202:
                return RelayResult(status="sent", status_code=202)
            if response.status_code >= _RETRYABLE_STATUS_FLOOR and attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
                continue
            last_detail = f"unexpected status {response.status_code}"
            return RelayResult(status="failed", status_code=last_status_code, detail=last_detail)

        return RelayResult(status="failed", status_code=last_status_code, detail=last_detail)

    async def aclose(self) -> None:
        await self._client.aclose()


_client: SelolibertariaClient | None = None


def get_selolibertaria_client() -> SelolibertariaClient:
    global _client
    if _client is None:
        _client = SelolibertariaClient()
    return _client


async def close_selolibertaria_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
