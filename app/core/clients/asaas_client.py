from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.decorators import log_call
from app.core.exceptions import UpstreamServiceError
from app.settings.config import settings

# Asaas occasionally 502s transiently (its own upstream, not asaas_payment
# itself) -- one retry with a short backoff absorbs that without making the
# caller (an admin request) implement its own retry logic.
_RETRYABLE_STATUS = 502
_MAX_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 0.3


class AsaasClient:
    """mTLS client of asaas_payment -- the only way devbutter_platform ever
    touches Asaas. devbutter_platform never sees the real ASAAS_API_KEY: it
    authenticates to asaas_payment's nginx with a client certificate issued
    by asaas_payment's own `scripts/generate_dev_certs.sh <cn>` (see
    docs/architecture/devbutter-platform-backend.md, "Contratos de integração").

    Layer A methods (create_customer, create_payment) call the generic
    pass-through route -- DevButter bills its own clients under DevButter's
    single Asaas account, not through a white-label subaccount. The
    subaccount/token/split/payment/audit methods below call asaas_payment's
    `/subaccounts/{id}/...` and `/_internal/audit-logs/...` routes instead,
    for the admin panel to operate merchant subaccounts (Layer B) -- see
    docs/plano-admin-asaas-integracao.md §2.2. This client's caller
    (AdminPaymentsService) is responsible for authorizing who may call
    these; asaas_payment only ever sees "it's devbutter_backend talking"
    (see docs/plano-admin-asaas-integracao.md §2.5).

    TODO(infra): the split/token/subaccount routes above are gated in
    asaas_payment by `require_admin`, which checks the caller's client
    certificate CN against `ADMIN_CLIENT_CNS` (not just `ALLOWED_CLIENT_CNS`).
    devbutter_backend's own CN (see settings.asaas_client_cert_path) must be
    added to asaas_payment's `ADMIN_CLIENT_CNS` env var, or every one of
    these calls gets a 403 from asaas_payment regardless of the caller
    being CurrentAdminAccount-gated here. This is an infra/config change,
    not a code change -- nothing in this repo can verify or apply it.
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

    async def create_payment(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/payments", json_body=payload)

    async def tokenize_payment_method(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Dedicated mTLS route; payload may contain card data, never log it."""
        return await self._request(
            "POST",
            "/internal/payment-methods/tokenize",
            json_body=payload,
            retry_on_502=False,
            timeout=settings.asaas_card_request_timeout_seconds,
        )

    async def pay_with_saved_payment_method(
        self, asaas_payment_id: str, provider_token: str
    ) -> dict[str, Any]:
        """Dedicated mTLS route. The provider token is never logged or returned."""
        return await self._request(
            "POST",
            "/internal/payment-methods/pay",
            json_body={"paymentId": asaas_payment_id, "creditCardToken": provider_token},
            retry_on_502=False,
            timeout=settings.asaas_card_request_timeout_seconds,
        )

    async def retrieve_payment(self, asaas_payment_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/internal/payment-methods/payments/{asaas_payment_id}",
            timeout=settings.asaas_card_request_timeout_seconds,
        )

    # -- Subaccounts --------------------------------------------------

    @log_call
    async def list_subaccounts(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", "/subaccounts", params=filters)

    @log_call
    async def get_subaccount(self, account_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/subaccounts/{account_id}")

    @log_call
    async def create_subaccount(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/subaccounts", json_body=payload)

    @log_call
    async def close_subaccount(self, account_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/subaccounts/{account_id}")

    @log_call
    async def resend_activation_link(self, account_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/subaccounts/{account_id}/resend-activation-link")

    # -- Access tokens --------------------------------------------------

    @log_call
    async def list_access_tokens(self, account_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/subaccounts/{account_id}/access-tokens")

    @log_call
    async def create_access_token(self, account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/subaccounts/{account_id}/access-tokens", json_body=payload
        )

    @log_call
    async def revoke_access_token(self, account_id: str, token_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/subaccounts/{account_id}/access-tokens/{token_id}")

    # -- Split rules --------------------------------------------------

    @log_call
    async def list_split_rules(self, account_id: str) -> list[dict[str, Any]]:
        # Unlike the other list_* methods, this hits asaas_payment's own
        # /split-rules resource (response_model=list[SplitRuleRead]) rather
        # than a passthrough of one of Asaas's own list endpoints -- the
        # body is a bare JSON array, not Asaas's {data, totalCount, ...}
        # envelope.
        return await self._request_list("GET", f"/subaccounts/{account_id}/split-rules")

    @log_call
    async def create_split_rule(self, account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/subaccounts/{account_id}/split-rules", json_body=payload
        )

    @log_call
    async def update_split_rule(
        self, account_id: str, rule_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", f"/subaccounts/{account_id}/split-rules/{rule_id}", json_body=payload
        )

    # -- Payments --------------------------------------------------

    @log_call
    async def list_payments(
        self, account_id: str, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._request("GET", f"/subaccounts/{account_id}/payments", params=filters)

    @log_call
    async def get_payment(self, account_id: str, payment_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/subaccounts/{account_id}/payments/{payment_id}")

    @log_call
    async def refund_payment(
        self, account_id: str, payment_id: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/subaccounts/{account_id}/payments/{payment_id}/refund",
            json_body=payload or {},
        )

    @log_call
    async def cancel_payment(self, account_id: str, payment_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/subaccounts/{account_id}/payments/{payment_id}")

    # -- Audit --------------------------------------------------

    @log_call
    async def get_audit_logs(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        # Also asaas_payment's own resource (response_model=list[AuditLogRead]),
        # a bare JSON array -- see list_split_rules.
        return await self._request_list("GET", "/_internal/audit-logs/all", params=filters)

    async def _request_list(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        body = await self._request(method, path, params=params)
        return body if isinstance(body, list) else []

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_on_502: bool = True,
        timeout: float | None = None,
    ) -> Any:
        attempt = 0
        while True:
            attempt += 1
            try:
                request_kwargs: dict[str, Any] = {"params": params, "json": json_body}
                if timeout is not None:
                    request_kwargs["timeout"] = timeout
                response = await self._client.request(method, path, **request_kwargs)
            except httpx.HTTPError as exc:
                raise UpstreamServiceError(f"asaas_payment request failed: {exc}") from exc

            if (
                retry_on_502
                and response.status_code == _RETRYABLE_STATUS
                and attempt < _MAX_ATTEMPTS
            ):
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                continue

            if response.status_code >= 400:
                raise UpstreamServiceError(
                    f"asaas_payment returned {response.status_code} for {method} {path}"
                )
            if not response.content:
                return {}
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
