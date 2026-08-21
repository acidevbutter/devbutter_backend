import uuid
from datetime import date
from decimal import Decimal

from app.core.decorators import log_errors
from app.orders.models import Order
from app.orders.service import OrderService
from app.quotes.models import Quote
from app.quotes.schemas import QuoteEdit
from app.quotes.service import QuoteService


@log_errors
class AdminService:
    """View over Quote plus the approval action that hands off to
    OrderService -- not its own entity/table (see architecture doc,
    "Review queue: not a separate entity, a status view over Quote").
    """

    def __init__(self, quote_service: QuoteService, order_service: OrderService):
        self.quote_service = quote_service
        self.order_service = order_service

    async def list_review_queue(self) -> list[Quote]:
        return await self.quote_service.list_review_queue()

    async def edit_quote(self, quote_id: uuid.UUID, payload: QuoteEdit) -> Quote:
        return await self.quote_service.edit(quote_id, payload)

    async def reject_quote(self, quote_id: uuid.UUID, reviewer_id: uuid.UUID) -> Quote:
        return await self.quote_service.reject(quote_id, reviewer_id)

    async def approve_quote(
        self,
        quote_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        *,
        amount: Decimal,
        due_date: date,
        billing_type: str,
    ) -> tuple[Quote, Order]:
        quote = await self.quote_service.approve(quote_id, reviewer_id)
        order = await self.order_service.create_from_approved_quote(
            quote, amount=amount, due_date=due_date, billing_type=billing_type
        )
        return quote, order
