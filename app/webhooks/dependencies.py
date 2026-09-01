from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.accounts.repository import AccountRepository
from app.core.clients.relay_client import RelayClient, get_relay_client
from app.core.dependencies import DbSession
from app.invoices.repository import InvoiceRepository
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
from app.payment_methods.repository import PaymentMethodRepository
from app.webhooks.repository import WebhookEventRepository
from app.webhooks.service import WebhookService


def get_webhook_service(
    db: DbSession,
    relay_client: Annotated[
        RelayClient,
        Depends(get_relay_client),
    ],
) -> WebhookService:
    notification_service = NotificationService(relay_client)
    return WebhookService(
        InvoiceRepository(db),
        OrderRepository(db),
        AccountRepository(db),
        notification_service,
        WebhookEventRepository(db),
        PaymentMethodRepository(db),
    )


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]
