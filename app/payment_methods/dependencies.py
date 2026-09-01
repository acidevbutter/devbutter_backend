from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.accounts.repository import AccountRepository
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.dependencies import DbSession
from app.invoices.repository import InvoiceRepository
from app.orders.repository import OrderRepository
from app.payment_methods.repository import PaymentMethodRepository
from app.payment_methods.service import PaymentMethodService


def get_payment_method_service(
    db: DbSession, asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)]
) -> PaymentMethodService:
    return PaymentMethodService(
        PaymentMethodRepository(db),
        InvoiceRepository(db),
        OrderRepository(db),
        AccountRepository(db),
        asaas_client,
    )


PaymentMethodServiceDep = Annotated[PaymentMethodService, Depends(get_payment_method_service)]
