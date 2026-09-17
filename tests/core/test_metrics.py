import json
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app import main
from app.core import metrics
from app.settings.middleware import RequestContextMiddleware


def test_route_template_uses_route_pattern_not_raw_path() -> None:
    matched = type(
        "RequestStub",
        (),
        {"scope": {"route": type("RouteStub", (), {"path": "/accounts/{account_id}"})()}},
    )()
    unmatched = type("RequestStub", (), {"scope": {}})()

    assert metrics.route_template(matched) == "/accounts/{account_id}"
    assert metrics.route_template(unmatched) == "<unmatched>"
    assert metrics.status_class(503) == "5xx"
    assert metrics.status_class(0) == "unknown"


def test_configure_metrics_is_callable_without_side_effects() -> None:
    metrics.configure_metrics()


async def test_emit_metrics_writes_one_json_line(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="metrics"):
        await metrics.emit_metrics(
            {"UpstreamService": "asaas_payment", "Operation": "POST", "StatusClass": "2xx"},
            [("UpstreamCallCount", 1, "Count")],
        )

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].message)
    assert payload["service.name"] == metrics.SERVICE_NAME
    assert payload["metric.namespace"] == metrics.NAMESPACE
    assert payload["environment"] == "development"
    assert payload["UpstreamService"] == "asaas_payment"
    assert payload["Operation"] == "POST"
    assert payload["StatusClass"] == "2xx"
    assert payload["UpstreamCallCount"] == 1
    assert payload["UpstreamCallCount.unit"] == "Count"
    assert {"CN", "AccountId", "Email"}.isdisjoint(payload)


async def test_emit_metrics_failure_does_not_raise(monkeypatch, caplog) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("stdout unavailable")

    monkeypatch.setattr(metrics, "_metrics_logger", type("_Boom", (), {"info": _boom})())

    with caplog.at_level(logging.ERROR):
        await metrics.emit_metrics({}, [])

    assert "Unable to emit metrics" in caplog.text


async def test_emit_metrics_background_schedules_emit(monkeypatch) -> None:
    emitted: list[tuple[dict[str, str], list[metrics.MetricValue]]] = []

    async def record_metrics(dimensions, values) -> None:
        emitted.append((dimensions, values))

    monkeypatch.setattr(metrics, "emit_metrics", record_metrics)

    metrics.emit_metrics_background({"Reason": "test"}, [("Foo", 1, "Count")])
    # emit_metrics_background schedules a task on the running loop rather
    # than awaiting it -- yield control once so it actually runs.
    import asyncio

    await asyncio.sleep(0)

    assert emitted == [({"Reason": "test"}, [("Foo", 1, "Count")])]


async def test_request_emits_one_metrics_event_without_breaking_response(caplog) -> None:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/accounts/{account_id}")
    async def get_account(account_id: str) -> dict[str, str]:
        return {"account_id": account_id}

    with caplog.at_level(logging.INFO, logger="metrics"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/accounts/8ee0cc8a-3a1a-4e3a-a376-f16ce3e6d031")

    assert response.status_code == 200
    metric_records = [r for r in caplog.records if r.name == "metrics"]
    assert len(metric_records) == 1
    payload = json.loads(metric_records[0].message)
    assert payload["Route"] == "/accounts/{account_id}"
    assert payload["RequestCount"] == 1


async def test_metrics_failure_does_not_break_response(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("stdout unavailable")

    monkeypatch.setattr(metrics, "_metrics_logger", type("_Boom", (), {"info": _boom})())

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/accounts/{account_id}")
    async def get_account(account_id: str) -> dict[str, str]:
        return {"account_id": account_id}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/accounts/8ee0cc8a-3a1a-4e3a-a376-f16ce3e6d031")

    assert response.status_code == 200


class _ReadyConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def execute(self, _query) -> None:
        return None


class _ReadyEngine:
    def connect(self) -> _ReadyConnection:
        return _ReadyConnection()


class _UnavailableEngine:
    def connect(self) -> _ReadyConnection:
        raise ConnectionError("database unavailable")


async def test_readiness_checks_database_connection(monkeypatch) -> None:
    emitted: list[tuple[dict[str, str], list[metrics.MetricValue]]] = []

    async def record_metrics(dimensions, values) -> None:
        emitted.append((dimensions, values))

    monkeypatch.setattr(main, "engine", _ReadyEngine())
    monkeypatch.setattr(main, "emit_metrics", record_metrics)

    response = await main.readiness()

    assert response == {"status": "ready"}
    assert emitted == []


async def test_readiness_emits_failure_metric_when_database_is_unavailable(monkeypatch) -> None:
    emitted: list[tuple[dict[str, str], list[metrics.MetricValue]]] = []

    async def record_metrics(dimensions, values) -> None:
        emitted.append((dimensions, values))

    monkeypatch.setattr(main, "engine", _UnavailableEngine())
    monkeypatch.setattr(main, "emit_metrics", record_metrics)

    response = await main.readiness()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert response.body == b'{"status":"unavailable"}'
    assert emitted == [({}, [("HealthCheckFailure", 1, "Count")])]
