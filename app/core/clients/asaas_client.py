from __future__ import annotations

from typing import Any

import httpx

from app.core.decorators import log_call
from app.core.exceptions import UpstreamServiceError
from app.settings.config import settings


class AsaasClient:
    """mTLS client of asaas_payment -- the only way devbutter_platform ever
    touches Asaas. devbutter_platform never sees the real ASAAS_API_KEY: it
    authenticates to asaas_payment's nginx with a client certificate issued
    by asaas_payment's own `scripts/generate_dev_certs.sh <cn>` (see
    docs/architecture/devbutter-platform-backend.md, "Contratos de integração").

    Calls the generic pass-through route (`/customers`, `/payments`, forwarded
    to Asaas's own `/v3/customers` / `/v3/payments`) rather than the
    `/subaccounts/{id}/...` routes -- Layer A bills DevButter's own clients
    under DevButter's single Asaas account, it does not act through a
    white-label subaccount. Layer B (ecommerce subaccounts) is out of scope
    for this implementation; see docs/architecture/architecture-ai-quote-client-portal.md §2.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.asaas_payment_base_url,
            timeout=settings.asaas_request_timeout_seconds,
            cert=(settings.asaas_client_cert_path, settings.asaas_client_key_path),
            verify=settings.asaas_ca_cert_path,
        )

    @log_call
    async def create_customer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/customers", json_body=payload)

    @log_call
    async def create_payment(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/payments", json_body=payload)

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json_body)
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(f"asaas_payment request failed: {exc}") from exc
        if response.status_code >= 400:
            raise UpstreamServiceError(
                f"asaas_payment returned {response.status_code} for {method} {path}: "
                f"{response.text}"
            )
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


_client: AsaasClient | None = None


def get_asaas_client() -> AsaasClient:
    global _client
    if _client is None:
        _client = AsaasClient()
    return _client


async def close_asaas_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
