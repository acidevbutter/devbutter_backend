import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.accounts.router import CurrentAccount, CurrentUser
from app.core.dependencies import DbSession
from app.core.exceptions import NotFoundError
from app.invoices.repository import InvoiceRepository
from app.invoices.schemas import InvoiceRead
from app.invoices.service import InvoiceService
from app.orders.repository import OrderRepository


def get_invoice_service(db: DbSession) -> InvoiceService:
    return InvoiceService(InvoiceRepository(db))


InvoiceServiceDep = Annotated[InvoiceService, Depends(get_invoice_service)]


def get_order_repository(db: DbSession) -> OrderRepository:
    return OrderRepository(db)


OrderRepositoryDep = Annotated[OrderRepository, Depends(get_order_repository)]

router = APIRouter(prefix="/orders/{order_id}/invoices", tags=["invoices"])
account_router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=list[InvoiceRead], summary="List invoices for an order")
async def list_order_invoices(
    order_id: uuid.UUID,
    account: CurrentAccount,
    user: CurrentUser,
    service: InvoiceServiceDep,
    order_repository: OrderRepositoryDep,
) -> list[InvoiceRead]:
    order = await order_repository.get(order_id)
    if order is None or (order.account_id != account.id and not user.is_admin):
        raise NotFoundError("Order not found")
    invoices = await service.list_for_order(order_id)
    return [InvoiceRead.model_validate(i) for i in invoices]


@account_router.get(
    "", response_model=list[InvoiceRead], summary="List the authenticated account's invoices"
)
async def list_account_invoices(
    account: CurrentAccount, service: InvoiceServiceDep
) -> list[InvoiceRead]:
    invoices = await service.list_for_account(account.id)
    return [InvoiceRead.model_validate(invoice) for invoice in invoices]
