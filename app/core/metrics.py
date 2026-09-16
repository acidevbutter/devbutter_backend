import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence

from starlette.requests import Request

from app.settings.config import settings

logger = logging.getLogger(__name__)
_metrics_logger = logging.getLogger("metrics")

NAMESPACE = "DevButter/PlatformBackend"
SERVICE_NAME = "devbutter_backend"

MetricValue = tuple[str, float | int, str]


def configure_metrics() -> None:
    """No-op retained for main.py's startup call. Metrics used to ship via
    CloudWatch Embedded Metric Format (aws_embedded_metrics), which needed a
    get_config() singleton configured before the first emit; the New Relic
    JSON sink below (see emit_metrics) carries no equivalent global state,
    so there is nothing left to configure here.
    """


def route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "<unmatched>"


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx" if 100 <= status_code <= 599 else "unknown"


async def emit_metrics(
    dimensions: Mapping[str, str], metric_values: Sequence[MetricValue]
) -> None:
    """Write one structured metric log line without allowing telemetry
    failures to affect work.

    This ships as a single JSON line via the standard `logging` module
    instead of CloudWatch EMF: the same ECS `awslogs` -> CloudWatch Logs
    pipeline this service already ships request logs through forwards to
    New Relic's log ingestion, which auto-parses JSON log lines and promotes
    their top-level keys to queryable log attributes -- no agent, no new
    dependency.
    """
    try:
        payload: dict[str, object] = {
            "timestamp": int(time.time() * 1000),
            "message": "metric_emitted",
            "service.name": SERVICE_NAME,
            "environment": settings.environment,
            "metric.namespace": NAMESPACE,
            **dimensions,
        }
        for name, value, unit in metric_values:
            payload[name] = value
            payload[f"{name}.unit"] = unit
        _metrics_logger.info(json.dumps(payload, default=str))
    except Exception:
        logger.exception("Unable to emit metrics")


def emit_metrics_background(
    dimensions: Mapping[str, str], metric_values: Sequence[MetricValue]
) -> None:
    """Schedule metric emission for synchronous code without delaying its caller."""
    try:
        asyncio.get_running_loop().create_task(emit_metrics(dimensions, metric_values))
    except RuntimeError:
        asyncio.run(emit_metrics(dimensions, metric_values))
