import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.admin.repository import AdminRepository
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

    def __init__(
        self,
        quote_service: QuoteService,
        order_service: OrderService | None,
        repository: AdminRepository,
    ):
        self.quote_service = quote_service
        self.order_service = order_service
        self.repository = repository

    async def list_review_queue(self) -> list[Quote]:
        return await self.quote_service.list_review_queue()

    async def list_all_quotes(
        self, *, status_: str | None, offset: int, limit: int
    ) -> tuple[list[Quote], int]:
        return await self.repository.list_quotes(status_=status_, offset=offset, limit=limit)

    async def list_clients(
        self, *, search: str | None, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        return await self.repository.list_clients(search=search, offset=offset, limit=limit)

    async def get_client(self, account_id: uuid.UUID) -> dict[str, Any] | None:
        return await self.repository.get_client(account_id)

    async def list_orders(
        self,
        *,
        status_: str | None,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self.repository.list_orders(
            status_=status_, search=search, offset=offset, limit=limit
        )

    async def list_invoices(
        self,
        *,
        status_: str | None,
        due_before: date | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, datetime | None]:
        return await self.repository.list_invoices(
            status_=status_, due_before=due_before, offset=offset, limit=limit
        )

    async def dashboard(self) -> dict[str, Any]:
        return await self.repository.dashboard_metrics()

    async def list_catalog(self) -> list[dict[str, Any]]:
        return await self.repository.list_catalog()

    async def list_conversations(
        self, *, has_quote: bool | None, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        return await self.repository.list_conversations(
            has_quote=has_quote, offset=offset, limit=limit
        )

    async def get_conversation(self, conversation_id: uuid.UUID) -> dict[str, Any] | None:
        return await self.repository.get_conversation(conversation_id)

    async def get_quote(self, quote_id: uuid.UUID) -> Quote:
        return await self.quote_service.get(quote_id)

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
        if self.order_service is None:
            raise RuntimeError("Order service is required for quote approval")
        order = await self.order_service.create_from_approved_quote(
            quote, amount=amount, due_date=due_date, billing_type=billing_type
        )
        return quote, order


@log_errors
class AdminReadService(AdminService):
    """Read-only admin view that does not initialize billing integrations."""

    def __init__(self, quote_service: QuoteService, repository: AdminRepository):
        super().__init__(quote_service, None, repository)
