import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.quotes.models import Quote


class QuoteRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, quote_id: uuid.UUID) -> Quote | None:
        return await self.session.get(Quote, quote_id)

    async def list_by_account(self, account_id: uuid.UUID) -> list[Quote]:
        result = await self.session.execute(
            select(Quote).where(Quote.account_id == account_id).order_by(Quote.created_at.desc())
        )
        return list(result.scalars())

    async def list_by_status(self, status_: str) -> list[Quote]:
        result = await self.session.execute(
            select(Quote).where(Quote.status == status_).order_by(Quote.created_at.asc())
        )
        return list(result.scalars())

    async def create(
        self,
        *,
        account_id: uuid.UUID,
        source: str,
        scope_summary: str,
        price_range: str | None,
        timeline: str | None,
        conversation_id: uuid.UUID | None = None,
    ) -> Quote:
        quote = Quote(
            account_id=account_id,
            conversation_id=conversation_id,
            source=source,
            scope_summary=scope_summary,
            price_range=price_range,
            timeline=timeline,
        )
        self.session.add(quote)
        await self.session.commit()
        await self.session.refresh(quote)
        return quote

    async def edit(self, quote: Quote, **fields: str | None) -> Quote:
        for field, value in fields.items():
            if value is not None:
                setattr(quote, field, value)
        quote.status = "edited"
        await self.session.commit()
        await self.session.refresh(quote)
        return quote

    async def set_status(
        self, quote: Quote, status_: str, reviewed_by: uuid.UUID | None = None
    ) -> Quote:
        quote.status = status_
        if reviewed_by is not None:
            quote.reviewed_by = reviewed_by
            quote.reviewed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(quote)
        return quote
