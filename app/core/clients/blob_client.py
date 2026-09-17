from __future__ import annotations

import json
from typing import Any, cast

import httpx

from app.core.decorators import log_call
from app.core.exceptions import UpstreamServiceError
from app.settings.config import settings


class BlobClient:
    """Uploads NFS-e documents to butter_blob. Never logs bytes or private URLs."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.butter_blob_base_url,
            timeout=settings.butter_blob_timeout_seconds,
        )

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"X-Blob-Namespace": settings.nfse_blob_namespace}
        key = settings.butter_blob_api_key
        if key.startswith("bbk_"):
            headers["Authorization"] = f"Bearer {key}"
        elif key:
            headers["X-Service-Api-Key"] = key
        return headers

    @log_call
    async def upload(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        visibility: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if not settings.butter_blob_base_url or not settings.butter_blob_api_key:
            raise UpstreamServiceError("Butter Blob is not configured")
        files = {"file": (filename, content, content_type)}
        data = {
            "visibility": visibility,
            "filename": filename,
            "metadata": json.dumps(metadata),
        }
        try:
            response = await self._client.post(
                "/internal/v1/blobs",
                headers=self._headers(),
                files=files,
                data=data,
            )
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(f"butter_blob request failed: {exc}") from exc
        if response.status_code >= 400:
            raise UpstreamServiceError(
                f"butter_blob returned {response.status_code}",
                status_code=response.status_code,
            )
        return cast(dict[str, Any], response.json())

    @log_call
    async def get(self, blob_id: str) -> dict[str, Any]:
        if not settings.butter_blob_base_url or not settings.butter_blob_api_key:
            raise UpstreamServiceError("Butter Blob is not configured")
        try:
            response = await self._client.get(
                f"/internal/v1/blobs/{blob_id}",
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(f"butter_blob request failed: {exc}") from exc
        if response.status_code >= 400:
            raise UpstreamServiceError(
                f"butter_blob returned {response.status_code}",
                status_code=response.status_code,
            )
        return cast(dict[str, Any], response.json())

    async def aclose(self) -> None:
        await self._client.aclose()


_client: BlobClient | None = None


def get_blob_client() -> BlobClient:
    global _client
    if _client is None:
        _client = BlobClient()
    return _client
