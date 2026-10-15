import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.orders.models import Order


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, order_id: uuid.UUID) -> Order | None:
        return await self.session.get(Order, order_id)

    async def get_by_quote(self, quote_id: uuid.UUID) -> Order | None:
        result = await self.session.execute(select(Order).where(Order.quote_id == quote_id))
        return result.scalar_one_or_none()

    async def delete(self, order: Order) -> None:
        await self.session.delete(order)
        await self.session.commit()

    async def list_by_account(self, account_id: uuid.UUID) -> list[Order]:
        result = await self.session.execute(
            select(Order).where(Order.account_id == account_id).order_by(Order.created_at.desc())
        )
        return list(result.scalars())

    async def create(self, *, quote_id: uuid.UUID, account_id: uuid.UUID) -> Order:
        order = Order(quote_id=quote_id, account_id=account_id)
        self.session.add(order)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(f"An order already exists for quote {quote_id}") from exc
        await self.session.refresh(order)
        return order

    async def set_status(self, order: Order, status_: str) -> Order:
        order.status = status_
        await self.session.commit()
        await self.session.refresh(order)
        return order
