import uuid

from fastapi import APIRouter

from app.accounts.dependencies import CurrentAccount, CurrentUser
from app.core.exceptions import NotFoundError
from app.invoices.dependencies import InvoiceServiceDep
from app.invoices.schemas import InvoiceRead
from app.orders.dependencies import OrderRepositoryDep

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
