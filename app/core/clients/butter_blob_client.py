from __future__ import annotations

import json
import time
from typing import Any, cast

import httpx

from app.core.decorators import log_call
from app.core.exceptions import UpstreamServiceError
from app.core.metrics import emit_metrics, status_class
from app.settings.config import settings


class ButterBlobClient:
    """Thin namespace-scoped HTTP client for butter_blob on the shared network."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.butter_blob_base_url,
            timeout=settings.butter_blob_request_timeout_seconds,
        )

    def _headers(self) -> dict[str, str]:
        if not settings.butter_blob_service_api_key:
            raise UpstreamServiceError("BUTTER_BLOB_SERVICE_API_KEY is not configured")
        return {
            "X-Service-Api-Key": settings.butter_blob_service_api_key,
            "X-Blob-Namespace": settings.butter_blob_namespace,
        }

    @log_call
    async def presign_upload(
        self,
        filename: str,
        content_type: str,
        *,
        visibility: str = "private",
        byte_size: int | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "filename": filename,
            "content_type": content_type,
            "visibility": visibility,
        }
        if byte_size is not None:
            body["byte_size"] = byte_size
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        return self._as_dict(
            await self._request("POST", "/internal/v1/blobs/presign", json=body)
        )

    @log_call
    async def complete_upload(self, blob_id: str) -> dict[str, Any]:
        return self._as_dict(
            await self._request("POST", f"/internal/v1/blobs/{blob_id}/complete")
        )

    @log_call
    async def upload_bytes(
        self, filename: str, data: bytes, content_type: str, **opts: Any
    ) -> dict[str, Any]:
        form: dict[str, str] = {"visibility": str(opts.pop("visibility", "private"))}
        for name in ("ttl_seconds",):
            value = opts.pop(name, None)
            if value is not None:
                form[name] = str(value)
        metadata = opts.pop("metadata", None)
        if metadata is not None:
            form["metadata"] = json.dumps(metadata)
        if opts:
            raise TypeError(f"Unsupported butter_blob upload options: {', '.join(opts)}")
        return self._as_dict(
            await self._request(
                "POST",
                "/internal/v1/blobs",
                data=form,
                files={"file": (filename, data, content_type)},
            )
        )

    @log_call
    async def get_blob(self, blob_id: str) -> dict[str, Any]:
        return self._as_dict(await self._request("GET", f"/internal/v1/blobs/{blob_id}"))

    @log_call
    async def download_url(
        self, blob_id: str, *, expires_in: int = 300, disposition: str = "attachment"
    ) -> str:
        body = self._as_dict(
            await self._request(
                "GET",
                f"/internal/v1/blobs/{blob_id}/download-url",
                params={"expires_in": expires_in, "disposition": disposition},
            )
        )
        url = body.get("url")
        if not isinstance(url, str):
            raise UpstreamServiceError("butter_blob returned an invalid download URL payload")
        return url

    @log_call
    async def delete_blob(self, blob_id: str, *, hard: bool = False) -> None:
        await self._request("DELETE", f"/internal/v1/blobs/{blob_id}", params={"hard": hard})

    def public_url(self, namespace: str, public_slug: str) -> str:
        return f"{settings.butter_blob_base_url.rstrip('/')}/v1/public/{namespace}/{public_slug}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        try:
            response = await self._client.request(method, path, headers=self._headers(), **kwargs)
        except httpx.HTTPError as exc:
            await self._record_metrics(method, started_at, "error")
            raise UpstreamServiceError(f"butter_blob request failed: {exc}") from exc
        if response.status_code >= 400:
            await self._record_metrics(method, started_at, status_class(response.status_code))
            raise UpstreamServiceError(
                f"butter_blob returned {response.status_code} for {method} {path}: {response.text}",
                status_code=response.status_code,
            )
        try:
            return response.json() if response.content else None
        finally:
            await self._record_metrics(method, started_at, status_class(response.status_code))

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise UpstreamServiceError("butter_blob returned an invalid response payload")
        return cast(dict[str, Any], value)

    async def _record_metrics(self, operation: str, started_at: float, result: str) -> None:
        values = [
            ("UpstreamCallCount", 1, "Count"),
            ("UpstreamLatency", (time.perf_counter() - started_at) * 1000, "Milliseconds"),
        ]
        if result == "error" or result in {"4xx", "5xx"}:
            values.append(("UpstreamErrorCount", 1, "Count"))
        await emit_metrics(
            {"UpstreamService": "butter_blob", "Operation": operation, "StatusClass": result},
            values,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


_client: ButterBlobClient | None = None


def get_butter_blob_client() -> ButterBlobClient:
    global _client
    if _client is None:
        _client = ButterBlobClient()
    return _client


async def close_butter_blob_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
