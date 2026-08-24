from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.decorators import log_call
from app.core.exceptions import UpstreamServiceError
from app.settings.config import settings


class ButterMindClient:
    """HTTP client of butter_mind's guided-diagnosis flow, same Docker network
    (`mind-api`). butter_mind produces the schema-validated draft
    (problem_summary, services_of_interest, budget_range, timeline) that
    conversations/service.py turns into a Quote(source="ai") -- butter_mind
    itself has no write access to Quote/Order/Invoice.

    submit_diagnosis's response (DiagnosisExtraction) also includes
    contact_name, contact_email, contact_phone, company_name and cnpj --
    gathered by the LLM during the chat itself (see butter_mind's
    DIAGNOSIS_SYSTEM_PROMPT/EXTRACTION_SYSTEM_PROMPT in
    app/diagnosis/service.py), not passed in as call arguments. This is what
    AccountService.get_or_create_from_extraction uses to create the visitor's
    User/Company/Account without a separate signup form -- see
    docs/plano-onboarding-conversa-primeiro.md.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.butter_mind_base_url,
            timeout=settings.butter_mind_request_timeout_seconds,
        )

    @log_call
    async def create_diagnosis_session(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/diagnosis/sessions", json_body={"session_id": session_id}
        )

    @log_call
    async def send_diagnosis_message(self, mind_session_id: str, content: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/diagnosis/sessions/{mind_session_id}/messages",
            json_body={"content": content},
        )

    @log_call
    async def submit_diagnosis(self, mind_session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/diagnosis/sessions/{mind_session_id}/submit")

    @log_call
    async def get_dashboard_overview(self) -> dict[str, Any]:
        return await self._internal_request("GET", "/diagnosis/internal/dashboard/overview")

    @log_call
    async def get_dashboard_settings(self) -> dict[str, Any]:
        return await self._internal_request("GET", "/diagnosis/internal/dashboard/settings")

    @log_call
    async def update_dashboard_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._internal_request(
            "PATCH", "/diagnosis/internal/dashboard/settings", json_body=payload
        )

    @log_call
    async def get_diagnosis_turn_metrics(self, mind_session_id: str) -> list[dict[str, Any]]:
        data = await self._internal_request(
            "GET", f"/diagnosis/sessions/{mind_session_id}/turn-metrics"
        )
        if not isinstance(data, list):
            raise UpstreamServiceError("butter_mind returned invalid turn metrics payload")
        return data

    @log_call
    async def stream_diagnosis_message(
        self, mind_session_id: str, content: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Relays butter_mind's SSE stream for a diagnosis turn as parsed event
        dicts (`{"type": "delta", ...}` / `{"type": "done", ...}`) -- used by
        the Socket.IO handlers to forward chunks to the browser as they arrive.
        """
        try:
            async with self._client.stream(
                "POST",
                f"/diagnosis/sessions/{mind_session_id}/messages/stream",
                json={"content": content},
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise UpstreamServiceError(
                        f"butter_mind returned {response.status_code} for stream messages: "
                        f"{body.decode()}"
                    )
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        yield json.loads(line.removeprefix("data: "))
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(f"butter_mind stream request failed: {exc}") from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method, path, json=json_body, params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(f"butter_mind request failed: {exc}") from exc
        if response.status_code >= 400:
            raise UpstreamServiceError(
                f"butter_mind returned {response.status_code} for {method} {path}: {response.text}"
            )
        return response.json()

    async def _internal_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if not settings.butter_mind_internal_api_key:
            raise UpstreamServiceError("BUTTER_MIND_INTERNAL_API_KEY is not configured")
        return await self._request(
            method,
            path,
            json_body=json_body,
            headers={"X-Internal-Api-Key": settings.butter_mind_internal_api_key},
        )

    async def aclose(self) -> None:
        await self._client.aclose()


_client: ButterMindClient | None = None


def get_butter_mind_client() -> ButterMindClient:
    global _client
    if _client is None:
        _client = ButterMindClient()
    return _client


async def close_butter_mind_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
