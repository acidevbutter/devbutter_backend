from __future__ import annotations

import json
from typing import Any

import boto3

from app.core.decorators import log_call
from app.core.exceptions import UpstreamServiceError
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
        if not settings.relay_sqs_queue_url:
            raise UpstreamServiceError("RELAY_SQS_QUEUE_URL is not configured")
        self._sqs.send_message(
            QueueUrl=settings.relay_sqs_queue_url,
            MessageBody=json.dumps({"template_key": template_key, "to": to, "payload": payload}),
            MessageAttributes={
                "project_id": {"StringValue": settings.relay_project_id, "DataType": "String"}
            },
        )


_client: RelayClient | None = None


def get_relay_client() -> RelayClient:
    global _client
    if _client is None:
        _client = RelayClient()
    return _client
