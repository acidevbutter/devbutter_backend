from __future__ import annotations

from typing import Any

from celery import Celery

from app.core.decorators import log_call
from app.core.exceptions import UpstreamServiceError
from app.settings.config import settings

TASK_NAME = "butter_relay.process_message"


class RelayClient:
    """Publishes "send this email" jobs onto butter_relay's shared Celery
    queue (same Redis broker butter_relay's own worker consumes from).
    devbutter_platform never calls an email provider directly and never
    renders templates itself -- it only builds {template_key, to, payload}
    and tags the message with its own `project_id`, exactly like every other
    app.SendMessage caller of butter_relay (see butter_relay/README.md).
    Templates (quote-approved, invoice-created, payment-confirmed) are
    registered in the relay admin, not hardcoded here.
    """

    def __init__(self) -> None:
        # A plain send_task client -- devbutter_backend never imports
        # butter_relay's own Celery app/task code (separate repos); it only
        # needs to match the task name, kwargs, and queue by convention.
        self._celery = Celery("devbutter_backend_relay_client", broker=settings.redis_url)

    @log_call
    def send(
        self,
        template_key: str,
        to: str,
        payload: dict[str, Any],
        *,
        project_id: str | None = None,
        queue: str | None = None,
    ) -> None:
        """`project_id`/`queue` override the platform defaults per call, so
        each Company's mail leaves through its own Relay project."""
        if not settings.redis_url:
            raise UpstreamServiceError("REDIS_URL is not configured")
        self._celery.send_task(
            TASK_NAME,
            kwargs={
                "project_id": project_id or settings.relay_project_id,
                "template_key": template_key,
                "to": to,
                "payload": payload,
            },
            queue=queue or settings.relay_queue_name,
        )


_client: RelayClient | None = None


def get_relay_client() -> RelayClient:
    global _client
    if _client is None:
        _client = RelayClient()
    return _client
