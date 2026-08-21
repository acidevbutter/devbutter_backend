import uuid
from typing import Any

from app.core.decorators import log_errors
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.quotes.models import Quote
from app.quotes.repository import QuoteRepository
from app.quotes.schemas import QuoteEdit, QuoteManualCreate


@log_errors
class QuoteService:
    def __init__(self, repository: QuoteRepository):
        self.repository = repository

    async def create_manual(self, payload: QuoteManualCreate) -> Quote:
        return await self.repository.create(
            account_id=payload.account_id,
            source="manual",
            scope_summary=payload.scope_summary,
            price_range=payload.price_range,
            timeline=payload.timeline,
        )

    async def create_from_conversation(
        self, *, account_id: uuid.UUID, conversation_id: uuid.UUID, extraction: dict[str, Any]
    ) -> Quote:
        services = extraction.get("services_of_interest") or []
        scope_summary = extraction["problem_summary"]
        if services:
            scope_summary = f"{scope_summary}\n\nServiços de interesse: {', '.join(services)}"
        return await self.repository.create(
            account_id=account_id,
            source="ai",
            scope_summary=scope_summary,
            price_range=extraction.get("budget_range"),
            timeline=extraction.get("timeline"),
            conversation_id=conversation_id,
        )

    async def get(self, quote_id: uuid.UUID) -> Quote:
        quote = await self.repository.get(quote_id)
        if quote is None:
            raise NotFoundError("Quote not found")
        return quote

    async def list_for_account(self, account_id: uuid.UUID) -> list[Quote]:
        return await self.repository.list_by_account(account_id)

    async def list_review_queue(self) -> list[Quote]:
        return await self.repository.list_by_status("draft")

    async def edit(self, quote_id: uuid.UUID, payload: QuoteEdit) -> Quote:
        quote = await self.get(quote_id)
        self._require_pending(quote)
        return await self.repository.edit(quote, **payload.model_dump(exclude_unset=True))

    async def approve(self, quote_id: uuid.UUID, reviewer_id: uuid.UUID) -> Quote:
        quote = await self.get(quote_id)
        self._require_pending(quote)
        return await self.repository.set_status(quote, "approved", reviewer_id)

    async def reject(self, quote_id: uuid.UUID, reviewer_id: uuid.UUID) -> Quote:
        quote = await self.get(quote_id)
        self._require_pending(quote)
        return await self.repository.set_status(quote, "rejected", reviewer_id)

    def _require_pending(self, quote: Quote) -> None:
        if quote.status not in ("draft", "edited"):
            raise ConflictError(f"Quote {quote.id} is already {quote.status}")
        if quote.status == "draft" and not quote.scope_summary:
            raise ValidationFailedError("Quote has no scope summary")
