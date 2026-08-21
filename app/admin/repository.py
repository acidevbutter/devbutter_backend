import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, AsaasCustomerLink
from app.conversations.models import Conversation
from app.invoices.models import Invoice
from app.orders.models import Order
from app.quotes.models import Quote
from app.service_catalog.models import ServiceCatalogEntry


class AdminRepository:
    """Read-only cross-account queries for admin views.

    Admin is a status/aggregate view over domain entities, not a separate
    persistence domain. Mutations remain in the owning domain services.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_clients(
        self, *, search: str | None, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        query = select(Account, AsaasCustomerLink.asaas_customer_id).outerjoin(
            AsaasCustomerLink, AsaasCustomerLink.account_id == Account.id
        )
        count_query = select(func.count()).select_from(Account)
        if search:
            term = f"%{search}%"
            condition = or_(
                Account.name.ilike(term),
                Account.email.ilike(term),
                Account.document.ilike(term),
            )
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = int((await self.session.execute(count_query)).scalar_one())
        result = await self.session.execute(
            query.order_by(Account.created_at.desc()).offset(offset).limit(limit)
        )
        rows = []
        for account, asaas_customer_id in result.all():
            rows.append(
                {
                    "account": account,
                    "asaas_customer_id": asaas_customer_id,
                    "quotes_count": await self._count(Quote, Quote.account_id == account.id),
                    "orders_count": await self._count(Order, Order.account_id == account.id),
                }
            )
        return rows, total

    async def get_client(self, account_id: uuid.UUID) -> dict[str, Any] | None:
        result = await self.session.execute(
            select(Account, AsaasCustomerLink.asaas_customer_id)
            .outerjoin(AsaasCustomerLink, AsaasCustomerLink.account_id == Account.id)
            .where(Account.id == account_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        account, asaas_customer_id = row
        counts = {
            "quotes": await self._count(Quote, Quote.account_id == account_id),
            "orders": await self._count(Order, Order.account_id == account_id),
            "invoices": await self._count(
                Invoice,
                Invoice.order_id.in_(
                    select(Order.id).where(Order.account_id == account_id)
                ),
            ),
            "catalog_entries": await self._count(
                ServiceCatalogEntry, ServiceCatalogEntry.account_id == account_id
            ),
            "conversations": await self._count(
                Conversation, Conversation.account_id == account_id
            ),
        }
        return {
            "account": account,
            "asaas_customer_id": asaas_customer_id,
            "counts": counts,
        }

    async def list_orders(
        self,
        *,
        status_: str | None,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        query = (
            select(Order, Account.name, func.coalesce(func.sum(Invoice.amount), Decimal("0")))
            .join(Account, Account.id == Order.account_id)
            .outerjoin(Invoice, Invoice.order_id == Order.id)
            .group_by(Order.id, Account.name)
        )
        count_query = select(func.count()).select_from(Order).join(
            Account, Account.id == Order.account_id
        )
        if status_:
            query = query.where(Order.status == status_)
            count_query = count_query.where(Order.status == status_)
        if search:
            condition = Account.name.ilike(f"%{search}%")
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = int((await self.session.execute(count_query)).scalar_one())
        result = await self.session.execute(
            query.order_by(Order.created_at.desc()).offset(offset).limit(limit)
        )
        return [
            {
                "order": order,
                "account_name": account_name,
                "amount": amount,
            }
            for order, account_name, amount in result.all()
        ], total

    async def list_invoices(
        self,
        *,
        status_: str | None,
        due_before: date | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, datetime | None]:
        query = (
            select(Invoice, Account.name)
            .join(Order, Order.id == Invoice.order_id)
            .join(Account, Account.id == Order.account_id)
        )
        count_query = select(func.count()).select_from(Invoice)
        if status_:
            query = query.where(Invoice.status == status_)
            count_query = count_query.where(Invoice.status == status_)
        if due_before:
            query = query.where(Invoice.due_date <= due_before)
            count_query = count_query.where(Invoice.due_date <= due_before)

        total = int((await self.session.execute(count_query)).scalar_one())
        result = await self.session.execute(
            query.order_by(Invoice.due_date.asc(), Invoice.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        synced_at = await self.session.scalar(select(func.max(Invoice.updated_at)))
        return [
            {"invoice": invoice, "account_name": account_name}
            for invoice, account_name in result.all()
        ], total, synced_at

    async def dashboard_metrics(self) -> dict[str, Any]:
        quotes_to_review = await self._count(Quote, Quote.status == "draft")
        active_orders = await self._count(Order, Order.status.in_(["created", "invoiced"]))
        open_statuses = ["PENDING", "OVERDUE"]
        receivable_open_amount = await self._sum(Invoice.amount, Invoice.status.in_(open_statuses))
        overdue_amount = await self._sum(Invoice.amount, Invoice.status == "OVERDUE")
        overdue_invoice_count = await self._count(Invoice, Invoice.status == "OVERDUE")
        overdue_client = await self.session.execute(
            select(Account.name)
            .join(Order, Order.account_id == Account.id)
            .join(Invoice, Invoice.order_id == Order.id)
            .where(Invoice.status == "OVERDUE")
            .order_by(Invoice.amount.desc())
            .limit(1)
        )
        return {
            "quotes_to_review": quotes_to_review,
            "active_orders": active_orders,
            "receivable_open_amount": receivable_open_amount,
            "overdue_amount": overdue_amount,
            "overdue_invoice_count": overdue_invoice_count,
            "overdue_client_name": overdue_client.scalar_one_or_none(),
            "recent_activity": await self._recent_activity(),
        }

    async def list_catalog(self) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(ServiceCatalogEntry, Account.name)
            .join(Account, Account.id == ServiceCatalogEntry.account_id)
            .order_by(ServiceCatalogEntry.created_at.desc())
        )
        return [
            {"entry": entry, "account_name": account_name}
            for entry, account_name in result.all()
        ]

    async def list_conversations(
        self, *, has_quote: bool | None, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        quote_id_subquery = (
            select(Quote.id)
            .where(Quote.conversation_id == Conversation.id)
            .order_by(Quote.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = select(Conversation, Account.name, quote_id_subquery).outerjoin(
            Account, Account.id == Conversation.account_id
        )
        count_query = select(func.count()).select_from(Conversation)
        if has_quote is True:
            query = query.where(quote_id_subquery.is_not(None))
            count_query = count_query.where(
                select(Quote.id)
                .where(Quote.conversation_id == Conversation.id)
                .exists()
            )
        elif has_quote is False:
            query = query.where(quote_id_subquery.is_(None))
            count_query = count_query.where(
                ~select(Quote.id)
                .where(Quote.conversation_id == Conversation.id)
                .exists()
            )

        total = int((await self.session.execute(count_query)).scalar_one())
        result = await self.session.execute(
            query.order_by(Conversation.created_at.desc()).offset(offset).limit(limit)
        )
        return [
            {
                "conversation": conversation,
                "account_name": account_name,
                "resulting_quote_id": resulting_quote_id,
                "message_count": len(conversation.transcript or []),
            }
            for conversation, account_name, resulting_quote_id in result.all()
        ], total

    async def get_conversation(self, conversation_id: uuid.UUID) -> dict[str, Any] | None:
        result = await self.session.execute(
            select(Conversation, Account.name)
            .outerjoin(Account, Account.id == Conversation.account_id)
            .where(Conversation.id == conversation_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        conversation, account_name = row
        quote_result = await self.session.execute(
            select(Quote)
            .where(Quote.conversation_id == conversation_id)
            .order_by(Quote.created_at.desc())
        )
        quote = quote_result.scalars().first()
        return {
            "conversation": conversation,
            "account_name": account_name,
            "quote": quote,
            "message_count": len(conversation.transcript or []),
        }

    async def list_quotes(
        self, *, status_: str | None, offset: int, limit: int
    ) -> tuple[list[Quote], int]:
        query = select(Quote)
        count_query = select(func.count()).select_from(Quote)
        if status_:
            query = query.where(Quote.status == status_)
            count_query = count_query.where(Quote.status == status_)
        total = int((await self.session.execute(count_query)).scalar_one())
        result = await self.session.execute(
            query.order_by(Quote.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars()), total

    async def _count(self, model: Any, *conditions: Any) -> int:
        query = select(func.count()).select_from(model)
        if conditions:
            query = query.where(*conditions)
        return int((await self.session.execute(query)).scalar_one())

    async def _sum(self, column: Any, condition: Any) -> Decimal:
        value = await self.session.scalar(
            select(func.coalesce(func.sum(column), 0)).where(condition)
        )
        return Decimal(str(value or 0))

    async def _recent_activity(self) -> list[dict[str, Any]]:
        activities: list[dict[str, Any]] = []
        quote_result = await self.session.execute(
            select(Quote).order_by(Quote.updated_at.desc()).limit(10)
        )
        for quote in quote_result.scalars():
            if quote.status in ("approved", "edited") and quote.reviewed_at:
                activities.append(
                    {
                        "kind": "quote_approved",
                        "message": f"Quote {quote.id} aprovado",
                        "at": quote.reviewed_at,
                    }
                )
        account_result = await self.session.execute(
            select(Account).order_by(Account.created_at.desc()).limit(10)
        )
        activities.extend(
            {
                "kind": "client_created",
                "message": f"Cliente {account.name} criado",
                "at": account.created_at,
            }
            for account in account_result.scalars()
        )
        return sorted(
            activities,
            key=lambda item: _utc_sort_key(item["at"]),
            reverse=True,
        )[:10]


def _utc_sort_key(value: datetime) -> datetime:
    """Normalize SQLite's naive timestamps before comparing them."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
