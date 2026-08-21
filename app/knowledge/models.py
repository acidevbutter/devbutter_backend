import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KnowledgeEntry(Base):
    """Admin-maintained service/pricing context for future AI quote guidance."""

    __tablename__ = "knowledge_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    price_min: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    price_max: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    typical_duration: Mapped[str] = mapped_column(String(100))
    ai_instructions: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
