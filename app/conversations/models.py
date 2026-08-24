import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    """A concierge chat session. Wraps a butter_mind guided-diagnosis session
    (`mind_session_id`, butter_mind's own session id) -- devbutter_platform
    never runs the LLM itself, it only stores the account link, a local
    transcript mirror for the client dashboard, and the resulting draft's
    status. `account_id` is nullable: a visitor can chat, and finish the whole
    diagnosis, before ever creating an account (see architecture doc §7,
    "Visitor: can chat with the AI, no account required"). It gets set at
    draft-quote time, either to an account created on the spot from the
    conversation's own extracted contact data or to an existing account with
    a matching email -- see AccountService.get_or_create_from_extraction and
    docs/plano-onboarding-conversa-primeiro.md.
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True, default=None
    )
    mind_session_id: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(20), default="active")
    transcript: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    # Mirrors butter_mind's last-turn ready_to_submit flag so a resumed chat
    # (see devbutter_frontend's db_conversation_id cookie) can restore the
    # 1e/ready-to-confirm UI state without an extra round trip.
    ready_to_submit: Mapped[bool] = mapped_column(Boolean, default=False)
    # Safe scope preview from the latest diagnosis turn. The final quote is
    # always re-extracted server-side at draft-quote time.
    draft_preview: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
