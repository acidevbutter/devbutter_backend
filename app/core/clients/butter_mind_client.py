from __future__ import annotations

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
    async def submit_diagnosis(
        self,
        mind_session_id: str,
        *,
        contact_name: str | None = None,
        contact_email: str | None = None,
        contact_phone: str | None = None,
        company_name: str | None = None,
    ) -> dict[str, Any]:
        params = {
            k: v
            for k, v in {
                "contact_name": contact_name,
                "contact_email": contact_email,
                "contact_phone": contact_phone,
                "company_name": company_name,
            }.items()
            if v is not None
        }
        return await self._request(
            "POST", f"/diagnosis/sessions/{mind_session_id}/submit", params=params
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(f"butter_mind request failed: {exc}") from exc
        if response.status_code >= 400:
            raise UpstreamServiceError(
                f"butter_mind returned {response.status_code} for {method} {path}: {response.text}"
            )
        return response.json()

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
