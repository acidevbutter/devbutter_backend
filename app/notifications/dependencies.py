from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.clients.relay_client import RelayClient, get_relay_client
from app.notifications.service import NotificationService


def get_notification_service(
    relay_client: Annotated[RelayClient, Depends(get_relay_client)],
) -> NotificationService:
    return NotificationService(relay_client)


NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]
