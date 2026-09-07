from __future__ import annotations

import httpx
import pytest

from app.core.clients import butter_blob_client as blob_module
from app.core.clients.butter_blob_client import ButterBlobClient
from app.core.exceptions import UpstreamServiceError
from app.settings.config import settings


class _Recorder:
    """Stands in for httpx.AsyncClient.request: records calls, replays queued responses."""

    def __init__(self, responses: list[httpx.Response]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def __call__(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        return self.responses.pop(0)


def _response(status_code: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=body if body is not None else {},
        request=httpx.Request("GET", "http://blob-api:8000/x"),
    )


@pytest.fixture(autouse=True)
def _blob_settings(monkeypatch):
    monkeypatch.setattr(settings, "butter_blob_service_api_key", "svc-key")
    monkeypatch.setattr(settings, "butter_blob_namespace", "devbutter_backend")
    monkeypatch.setattr(settings, "butter_blob_base_url", "http://blob-api:8000")


@pytest.fixture
def emitted(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _fake_emit(dimensions, metric_values):
        calls.append({"dimensions": dict(dimensions), "values": list(metric_values)})

    monkeypatch.setattr(blob_module, "emit_metrics", _fake_emit)
    return calls


def _client_with(
    monkeypatch, responses: list[httpx.Response]
) -> tuple[ButterBlobClient, _Recorder]:
    client = ButterBlobClient()
    recorder = _Recorder(responses)
    monkeypatch.setattr(client._client, "request", recorder)
    return client, recorder


async def test_presign_upload_returns_body_and_sends_both_headers(monkeypatch, emitted):
    body = {"blob_id": "b1", "upload_url": "http://blob-api:8000/put", "method": "PUT"}
    client, recorder = _client_with(monkeypatch, [_response(201, body)])

    result = await client.presign_upload("a.png", "image/png", visibility="public", byte_size=10)

    assert result == body
    call = recorder.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/internal/v1/blobs/presign"
    assert call["headers"]["X-Service-Api-Key"] == "svc-key"
    assert call["headers"]["X-Blob-Namespace"] == "devbutter_backend"
    assert call["json"] == {
        "filename": "a.png",
        "content_type": "image/png",
        "visibility": "public",
        "byte_size": 10,
    }
    assert emitted[0]["dimensions"]["UpstreamService"] == "butter_blob"
    assert emitted[0]["dimensions"]["Operation"] == "POST"
    assert emitted[0]["dimensions"]["StatusClass"] == "2xx"


@pytest.mark.parametrize("status_code", [404, 413, 507])
async def test_non_2xx_raises_upstream_error_with_status(monkeypatch, emitted, status_code):
    client, _ = _client_with(monkeypatch, [_response(status_code, {"detail": "nope"})])

    with pytest.raises(UpstreamServiceError) as excinfo:
        await client.presign_upload("a.png", "image/png")

    assert excinfo.value.status_code == status_code
    assert emitted[0]["dimensions"]["StatusClass"] == f"{status_code // 100}xx"
    assert any(name == "UpstreamErrorCount" for name, _, _ in emitted[0]["values"])


async def test_download_url_returns_url_field(monkeypatch, emitted):
    client, recorder = _client_with(
        monkeypatch, [_response(200, {"url": "http://signed", "expires_at": "later"})]
    )

    url = await client.download_url("b1", expires_in=120, disposition="inline")

    assert url == "http://signed"
    call = recorder.calls[0]
    assert call["path"] == "/internal/v1/blobs/b1/download-url"
    assert call["params"] == {"expires_in": 120, "disposition": "inline"}


async def test_delete_blob_sends_hard_flag_and_no_body(monkeypatch, emitted):
    no_content = httpx.Response(204, request=httpx.Request("DELETE", "http://x"))
    client, recorder = _client_with(monkeypatch, [no_content])

    assert await client.delete_blob("b1", hard=True) is None
    assert recorder.calls[0]["method"] == "DELETE"
    assert recorder.calls[0]["params"] == {"hard": True}


async def test_missing_service_api_key_raises_before_any_http(monkeypatch, emitted):
    monkeypatch.setattr(settings, "butter_blob_service_api_key", "")
    client, recorder = _client_with(monkeypatch, [_response(201, {})])

    with pytest.raises(UpstreamServiceError):
        await client.presign_upload("a.png", "image/png")

    assert recorder.calls == []
    assert emitted == []


def test_public_url_is_a_pure_string_build():
    client = ButterBlobClient()
    assert (
        client.public_url("devbutter_backend", "abc123")
        == "http://blob-api:8000/v1/public/devbutter_backend/abc123"
    )
