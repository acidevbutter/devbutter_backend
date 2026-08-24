import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.accounts.repository import AccountRepository
from app.accounts.router import CurrentAccount, CurrentUser
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.clients.relay_client import RelayClient, get_relay_client
from app.core.dependencies import DbSession
from app.core.exceptions import NotFoundError
from app.invoices.repository import InvoiceRepository
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
from app.orders.schemas import OrderRead
from app.orders.service import OrderService


def get_notification_service(
    relay_client: Annotated[RelayClient, Depends(get_relay_client)],
) -> NotificationService:
    return NotificationService(relay_client)


def get_order_service(
    db: DbSession,
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
    notification_service: Annotated[NotificationService, Depends(get_notification_service)],
) -> OrderService:
    return OrderService(
        OrderRepository(db),
        AccountRepository(db),
        InvoiceRepository(db),
        asaas_client,
        notification_service,
    )


OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderRead], summary="List the authenticated account's orders")
async def list_my_orders(account: CurrentAccount, service: OrderServiceDep) -> list[OrderRead]:
    orders = await service.list_for_account(account.id)
    return [OrderRead.model_validate(o) for o in orders]


@router.get("/{order_id}", response_model=OrderRead, summary="Get an order")
async def get_order(
    order_id: uuid.UUID,
    account: CurrentAccount,
    user: CurrentUser,
    service: OrderServiceDep,
) -> OrderRead:
    order = await service.get(order_id)
    if order.account_id != account.id and not user.is_admin:
        raise NotFoundError("Order not found")
    return OrderRead.model_validate(order)
