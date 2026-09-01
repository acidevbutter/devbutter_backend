from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.accounts.repository import AccountRepository
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.dependencies import DbSession
from app.invoices.repository import InvoiceRepository
from app.notifications.dependencies import get_notification_service
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
from app.orders.service import OrderService


def get_order_repository(db: DbSession) -> OrderRepository:
    return OrderRepository(db)


OrderRepositoryDep = Annotated[OrderRepository, Depends(get_order_repository)]


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
