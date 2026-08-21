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
