import logging
import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.db.session import engine
from app.settings.config import settings

logger = logging.getLogger(__name__)


def _configure_http_semantic_conventions() -> None:
    # New Relic needs stable HTTP attributes such as http.route to derive APM transactions.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")


def _instrument_fastapi_app(
    app: FastAPI,
    *,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
) -> None:
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        excluded_urls="health,health/ready",
    )


def configure_telemetry(app: FastAPI) -> bool:
    """Send APM traces and metrics through the configured OTLP/HTTP endpoint."""
    _configure_http_semantic_conventions()
    if not settings.otel_enabled:
        return False

    resource = Resource.create(
        {
            SERVICE_NAME: settings.otel_service_name,
            "deployment.environment.name": settings.environment,
        }
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15_000)
        ],
    )
    metrics.set_meter_provider(meter_provider)

    _instrument_fastapi_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
    HTTPXClientInstrumentor().instrument(tracer_provider=tracer_provider)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    logger.info("OpenTelemetry enabled", extra={"service": settings.otel_service_name})
    return True
