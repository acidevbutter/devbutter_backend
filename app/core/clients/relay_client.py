from __future__ import annotations

import json
import time
from typing import Any

import boto3

from app.core.decorators import log_call
from app.core.exceptions import UpstreamServiceError
from app.core.metrics import emit_metrics_background
from app.settings.config import settings


class RelayClient:
    """Publishes "send this email" jobs onto butter_relay's shared SQS queue.
    devbutter_platform never calls an email provider directly and never
    renders templates itself -- it only builds {template_key, to, payload}
    and tags the message with its own `project_id`, exactly like every other
    app.SendMessage caller of butter_relay (see butter_relay/README.md).
    Templates (quote-approved, invoice-created, payment-confirmed) are
    registered in the relay admin, not hardcoded here.
    """

    def __init__(self) -> None:
        self._sqs = boto3.client(
            "sqs",
            region_name=settings.aws_region,
            endpoint_url=settings.relay_sqs_endpoint_url,
        )

    @log_call
    def send(self, template_key: str, to: str, payload: dict[str, Any]) -> None:
        started_at = time.perf_counter()
        if not settings.relay_sqs_queue_url:
            self._record_metrics(started_at, "error")
            raise UpstreamServiceError("RELAY_SQS_QUEUE_URL is not configured")
        try:
            self._sqs.send_message(
                QueueUrl=settings.relay_sqs_queue_url,
                MessageBody=json.dumps(
                    {"template_key": template_key, "to": to, "payload": payload}
                ),
                MessageAttributes={
                    "project_id": {"StringValue": settings.relay_project_id, "DataType": "String"}
                },
            )
        except Exception:
            self._record_metrics(started_at, "error")
            raise
        self._record_metrics(started_at, "2xx")

    def _record_metrics(self, started_at: float, result: str) -> None:
        values = [
            ("UpstreamCallCount", 1, "Count"),
            ("UpstreamLatency", (time.perf_counter() - started_at) * 1000, "Milliseconds"),
        ]
        if result == "error":
            values.append(("UpstreamErrorCount", 1, "Count"))
        emit_metrics_background(
            {
                "UpstreamService": "butter_relay",
                "Operation": "send_message",
                "StatusClass": result,
            },
            values,
        )


_client: RelayClient | None = None


def get_relay_client() -> RelayClient:
    global _client
    if _client is None:
        _client = RelayClient()
    return _client
