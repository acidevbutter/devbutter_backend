import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    """A concierge chat session. Wraps a butter_mind guided-diagnosis session
    (`mind_session_id`, butter_mind's own session id) -- devbutter_platform
    never runs the LLM itself, it only stores the account link, a local
    transcript mirror for the client dashboard, and the resulting draft's
    status. `account_id` is nullable: a visitor can chat before creating an
    account (see architecture doc §7, "Visitor: can chat with the AI, no
    account required"); it must be set before the conversation can produce a
    persisted Quote (see quotes/service.py).
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True, default=None
    )
    mind_session_id: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(20), default="active")
    transcript: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
