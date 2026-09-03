import asyncio
import logging
import os
from collections.abc import Mapping, Sequence

from aws_embedded_metrics.config import get_config
from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.logger.metrics_logger_factory import create_metrics_logger
from starlette.requests import Request

from app.settings.config import settings

logger = logging.getLogger(__name__)

NAMESPACE = "DevButter/PlatformBackend"
SERVICE_NAME = "devbutter_backend"
LOG_GROUP_NAME = "/ecs/devbutter/staging/backend"

MetricValue = tuple[str, float | int, str]


def configure_metrics() -> None:
    """Configure EMF once at startup while allowing deployment overrides.

    Forces the "Local" EMF environment (stdout sink) by default: this
    architecture ships metrics via the ECS `awslogs` log driver -> CloudWatch
    Logs auto-extraction, never a CloudWatch Agent sidecar. Without this
    override the library's environment autodetection falls through to the
    "Agent" sink (TCP to a nonexistent daemon), which drops every metric and
    spams "Connection refused"/"Broken pipe" on every request, locally and in
    production alike.
    """
    emf_config = get_config()
    emf_config.namespace = os.getenv("AWS_EMF_NAMESPACE", NAMESPACE)
    emf_config.service_name = os.getenv("AWS_EMF_SERVICE_NAME", SERVICE_NAME)
    emf_config.log_group_name = os.getenv("AWS_EMF_LOG_GROUP_NAME", LOG_GROUP_NAME)
    emf_config.environment = os.getenv("AWS_EMF_ENVIRONMENT", "Local")


def route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "<unmatched>"


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx" if 100 <= status_code <= 599 else "unknown"


async def emit_metrics(
    dimensions: Mapping[str, str], metric_values: Sequence[MetricValue]
) -> None:
    """Write one EMF event without allowing telemetry failures to affect work."""
    try:
        metrics: MetricsLogger = create_metrics_logger()
        coarse_dimensions = {"Environment": settings.environment, "Service": SERVICE_NAME}
        metrics.set_dimensions(coarse_dimensions, {**coarse_dimensions, **dimensions})
        for name, value, unit in metric_values:
            metrics.put_metric(name, value, unit)
        await metrics.flush()
    except Exception:
        logger.exception("Unable to flush embedded metrics")


def emit_metrics_background(
    dimensions: Mapping[str, str], metric_values: Sequence[MetricValue]
) -> None:
    """Schedule EMF emission for synchronous code without delaying its caller."""
    try:
        asyncio.get_running_loop().create_task(emit_metrics(dimensions, metric_values))
    except RuntimeError:
        asyncio.run(emit_metrics(dimensions, metric_values))
