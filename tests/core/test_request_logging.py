import json
import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.settings.logging_config import JsonFormatter, setup_logging
from app.settings.middleware import RequestContextMiddleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/accounts/{account_id}")
    async def get_account(account_id: str) -> dict[str, str]:
        return {"account_id": account_id}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("credentials: super-secret-token")

    return app


@pytest.fixture(autouse=True)
def _propagate_app_request_logger(caplog):
    # caplog attaches its handler to the root logger; make sure the request
    # logger's own records reach it regardless of dictConfig handler wiring.
    logger = logging.getLogger("app.request")
    logger.propagate = True
    yield


async def test_one_completion_event_per_successful_request(caplog) -> None:
    app = _build_app()
    with caplog.at_level(logging.INFO, logger="app.request"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/accounts/8ee0cc8a-3a1a-4e3a-a376-f16ce3e6d031")

    assert response.status_code == 200

    request_records = [r for r in caplog.records if r.name == "app.request"]
    assert len(request_records) == 1

    record = request_records[0]
    assert record.event == "http_request_completed"
    assert record.method == "GET"
    assert record.route == "/accounts/{account_id}"
    assert record.status_code == 200
    assert isinstance(record.duration_ms, float)
    assert record.request_id
    assert record.service
    assert record.environment


async def test_one_failure_event_per_unhandled_exception_no_duplicate(caplog) -> None:
    app = _build_app()
    with caplog.at_level(logging.INFO, logger="app.request"):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/boom")

    assert response.status_code == 500

    request_records = [r for r in caplog.records if r.name == "app.request"]
    assert len(request_records) == 1

    record = request_records[0]
    assert record.event == "http_request_failed"
    assert record.route == "/boom"
    assert record.status_code == 500
    assert record.request_id
    assert record.exc_info is not None

    # The traceback/message must never leak into a *second* log via the
    # str() of the exception -- and the one event we do emit must not
    # editorialize the exception message as a plain log field either.
    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)
    assert payload["message"] == "request failed"


def test_json_formatter_emits_one_parseable_object_with_expected_fields() -> None:
    setup_logging("INFO")
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="app.request",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request completed",
        args=(),
        exc_info=None,
    )
    record.event = "http_request_completed"
    record.service = "devbutter_backend"
    record.environment = "development"
    record.request_id = "11111111-1111-1111-1111-111111111111"
    record.method = "GET"
    record.route = "/health"
    record.status_code = 200
    record.duration_ms = 1.2

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.request"
    assert payload["event"] == "http_request_completed"
    assert payload["service"] == "devbutter_backend"
    assert payload["environment"] == "development"
    assert payload["request_id"] == "11111111-1111-1111-1111-111111111111"
    assert payload["method"] == "GET"
    assert payload["route"] == "/health"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 1.2
    assert "timestamp" in payload


def test_uvicorn_access_logger_is_quiet_and_does_not_propagate() -> None:
    setup_logging("INFO")
    uvicorn_access = logging.getLogger("uvicorn.access")

    assert uvicorn_access.level == logging.WARNING
    assert uvicorn_access.propagate is False
