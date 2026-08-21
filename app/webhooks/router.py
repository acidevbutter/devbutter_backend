from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from app.accounts.repository import AccountRepository
from app.core.clients.relay_client import RelayClient, get_relay_client
from app.core.dependencies import DbSession
from app.core.exceptions import UnauthorizedError
from app.invoices.repository import InvoiceRepository
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
from app.settings.config import settings
from app.webhooks.schemas import AsaasWebhookEvent
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
    )


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/asaas",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Receive Asaas payment status change events",
)
async def receive_asaas_webhook(
    payload: AsaasWebhookEvent,
    service: WebhookServiceDep,
    asaas_access_token: Annotated[
        str | None,
        Header(alias="asaas-access-token"),
    ] = None,
) -> None:
    if (
        not settings.asaas_webhook_access_token
        or asaas_access_token != settings.asaas_webhook_access_token
    ):
        raise UnauthorizedError("Invalid asaas-access-token")
    await service.handle_payment_event(payload)
