from unittest.mock import Mock

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from starlette.testclient import TestClient

from app.core import telemetry
from app.core.telemetry import configure_telemetry
from app.db.session import engine
from app.settings.config import settings


def test_telemetry_is_disabled_without_runtime_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(settings, "otel_enabled", False)

    assert configure_telemetry(FastAPI()) is False


def test_telemetry_service_name_matches_deployment_identity() -> None:
    assert settings.otel_service_name == "devbutter_backend"


def test_telemetry_instruments_fastapi_httpx_and_sqlalchemy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "otel_enabled", True)
    tracer_provider = Mock()
    meter_provider = Mock()
    fastapi_instrument = Mock()
    httpx_instrument = Mock()
    sqlalchemy_instrument = Mock()
    monkeypatch.setattr(telemetry, "TracerProvider", Mock(return_value=tracer_provider))
    monkeypatch.setattr(telemetry, "MeterProvider", Mock(return_value=meter_provider))
    monkeypatch.setattr(telemetry, "BatchSpanProcessor", Mock())
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", Mock())
    monkeypatch.setattr(telemetry, "OTLPMetricExporter", Mock())
    monkeypatch.setattr(telemetry, "PeriodicExportingMetricReader", Mock())
    monkeypatch.setattr(trace, "set_tracer_provider", Mock())
    monkeypatch.setattr(metrics, "set_meter_provider", Mock())
    monkeypatch.setattr(FastAPIInstrumentor, "instrument_app", fastapi_instrument)
    monkeypatch.setattr(HTTPXClientInstrumentor, "instrument", httpx_instrument)
    monkeypatch.setattr(SQLAlchemyInstrumentor, "instrument", sqlalchemy_instrument)
    app = FastAPI()

    assert configure_telemetry(app) is True

    fastapi_instrument.assert_called_once_with(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        excluded_urls="health,health/ready",
    )
    httpx_instrument.assert_called_once_with(tracer_provider=tracer_provider)
    sqlalchemy_instrument.assert_called_once_with(engine=engine.sync_engine)


def test_http_server_metric_has_new_relic_transaction_attributes() -> None:
    configure_telemetry(FastAPI())
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    app = FastAPI()

    @app.get("/apm-probe/{probe_id}")
    def apm_probe(probe_id: str) -> dict[str, str]:
        return {"probe_id": probe_id}

    telemetry._instrument_fastapi_app(app, meter_provider=meter_provider)
    with TestClient(app) as client:
        response = client.get("/apm-probe/42")

    assert response.status_code == 200
    exported = reader.get_metrics_data()
    assert exported is not None
    metric = next(
        metric
        for resource_metrics in exported.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
        if metric.name == "http.server.request.duration"
    )
    attributes = metric.data.data_points[0].attributes
    assert attributes is not None
    assert attributes["http.request.method"] == "GET"
    assert attributes["http.route"] == "/apm-probe/{probe_id}"
    assert attributes["http.response.status_code"] == 200
