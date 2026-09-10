from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app import main
from app.core import metrics
from app.settings.middleware import RequestContextMiddleware


class _MetricsRecorder:
    def __init__(self, flush_error: Exception | None = None) -> None:
        self.dimensions: dict[str, str] = {}
        self.values: list[tuple[str, float | int, str]] = []
        self.flush_error = flush_error

    def put_dimensions(self, dimensions: dict[str, str]) -> None:
        self.dimensions.update(dimensions)

    def set_dimensions(self, *dimension_sets: dict[str, str]) -> None:
        self.dimension_sets = list(dimension_sets)
        for dimension_set in dimension_sets:
            self.dimensions.update(dimension_set)

    def put_metric(self, name: str, value: float | int, unit: str) -> None:
        self.values.append((name, value, unit))

    async def flush(self) -> None:
        if self.flush_error:
            raise self.flush_error


def test_route_template_uses_route_pattern_not_raw_path() -> None:
    request = type(
        "RequestStub",
        (),
        {"scope": {"route": type("RouteStub", (), {"path": "/accounts/{account_id}"})()}},
    )()

    assert metrics.route_template(request) == "/accounts/{account_id}"
    assert metrics.status_class(503) == "5xx"
    assert metrics.status_class(0) == "unknown"


async def test_emit_metrics_uses_only_low_cardinality_dimensions(monkeypatch) -> None:
    recorder = _MetricsRecorder()
    monkeypatch.setattr(metrics, "create_metrics_logger", lambda: recorder)

    await metrics.emit_metrics(
        {"UpstreamService": "asaas_payment", "Operation": "POST", "StatusClass": "2xx"},
        [("UpstreamCallCount", 1, "Count")],
    )

    assert recorder.dimensions == {
        "Environment": "development",
        "Service": metrics.SERVICE_NAME,
        "UpstreamService": "asaas_payment",
        "Operation": "POST",
        "StatusClass": "2xx",
    }
    assert {"CN", "AccountId", "Email"}.isdisjoint(recorder.dimensions)


def test_configure_metrics_applies_deployment_overrides(monkeypatch) -> None:
    emf_config = type("EmfConfig", (), {})()
    monkeypatch.setattr(metrics, "get_config", lambda: emf_config)
    monkeypatch.setenv("AWS_EMF_NAMESPACE", "Test/Backend")
    monkeypatch.setenv("AWS_EMF_SERVICE_NAME", "test-backend")
    monkeypatch.setenv("AWS_EMF_LOG_GROUP_NAME", "/test/backend")

    metrics.configure_metrics()

    assert emf_config.namespace == "Test/Backend"
    assert emf_config.service_name == "test-backend"
    assert emf_config.log_group_name == "/test/backend"


async def test_metrics_flush_failure_does_not_break_response(monkeypatch) -> None:
    recorder = _MetricsRecorder(flush_error=PermissionError("stdout unavailable"))
    monkeypatch.setattr(metrics, "create_metrics_logger", lambda: recorder)

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/accounts/{account_id}")
    async def get_account(account_id: str) -> dict[str, str]:
        return {"account_id": account_id}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/accounts/8ee0cc8a-3a1a-4e3a-a376-f16ce3e6d031")

    assert response.status_code == 200
    assert recorder.dimensions["Route"] == "/accounts/{account_id}"
    assert ("RequestCount", 1, "Count") in recorder.values


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
