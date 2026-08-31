import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WebhookEvent(Base):
    """Durable audit record for every accepted webhook-shaped delivery:
    Asaas payment/account webhooks, and (per CONTRATO-HMAC.md's "idempotencia
    de negocio... event_id em asaas_relay_event / WebhookEvent") the
    Selolibertaria -> DevButter reactive rotate-key request handled in
    app/integrations/router.py, even though that one isn't itself an Asaas
    webhook. `event_id` is the emitter-assigned idempotency key when the
    delivery carries one (Asaas events don't map one field-for-field today;
    the reactive rotate-key request always sets it) -- nullable + unique so
    it's an opt-in dedupe key, not a requirement for every row.
    """

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    event_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, default=None)
    related_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), index=True, default=None
    )
    processed_ok: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
