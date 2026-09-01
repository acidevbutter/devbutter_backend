import uuid

from fastapi import APIRouter

from app.accounts.dependencies import CurrentAccount, CurrentUser
from app.core.exceptions import NotFoundError
from app.orders.dependencies import OrderServiceDep
from app.orders.schemas import OrderRead

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
