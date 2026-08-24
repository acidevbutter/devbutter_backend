import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Quote(Base):
    """A draft scope+price+timeline. `source` distinguishes an AI-drafted quote
    (conversation_id set) from one Marcos set directly (conversation_id null)
    -- both are first-class, not one a fallback for the other. Nothing here
    reaches Order/Invoice/Asaas until an admin approves it (see admin/service.py).
    """

    __tablename__ = "quotes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id"), default=None
    )
    # Human-readable "COT-1000" id, assigned from QuoteReferenceCounter at
    # creation -- see QuoteRepository._next_reference. Not the primary key so
    # nothing else has to change if the format ever does.
    reference: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(10))  # "ai" | "manual"
    status: Mapped[str] = mapped_column(
        String(20), default="draft"
    )  # draft|approved|edited|rejected
    scope_summary: Mapped[str] = mapped_column(Text)
    price_range: Mapped[str | None] = mapped_column(String(100), default=None)
    timeline: Mapped[str | None] = mapped_column(String(100), default=None)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class QuoteReferenceCounter(Base):
    """Singleton row (id=1) handing out the next Quote.reference number.
    A plain table instead of a Postgres SEQUENCE so it behaves identically
    under the in-memory SQLite tests (schema built from these models via
    Base.metadata.create_all, not by running Alembic) and real Postgres --
    same reasoning app/knowledge/repository.py documents for avoiding a
    Postgres-only similarity operator.
    """

    __tablename__ = "quote_reference_counters"

    id: Mapped[int] = mapped_column(primary_key=True)
    next_value: Mapped[int] = mapped_column(default=1000)
