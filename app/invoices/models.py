import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.db.base import Base


class Invoice(Base):
    """Local mirror of an Asaas cobrança -- created once at Order approval
    (see orders/service.py) and kept in sync via the Asaas webhook
    (see webhooks/service.py). devbutter_platform is the source of truth for
    the client-facing status; Asaas is the source of truth for the payment
    itself.
    """

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    # A Layer A "invoice" is an Asaas payment/charge, not an NFS-e. Keep the
    # legacy Python/DB name for compatibility while publishing the canonical
    # mirror vocabulary through asaas_charge_id.
    asaas_invoice_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    asaas_charge_id = synonym("asaas_invoice_id")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    # Explicit marker: never infer NFS-e from a paid charge alone (fatia 01).
    requires_nfse: Mapped[bool] = mapped_column(Boolean, default=False)
    due_date: Mapped[date] = mapped_column(Date)
    payment_link: Mapped[str | None] = mapped_column(String(500), default=None)
    # PIX / BOLETO / CREDIT_CARD / UNDEFINED as Asaas reports it -- lets the portal
    # label the charge correctly instead of guessing from the payment link.
    billing_type: Mapped[str | None] = mapped_column(String(20), default=None)
    # Only a digest of the provider event is retained here. Raw provider
    # payloads (which can contain customer data) become asaas_payment's
    # encrypted canonical mirror after the migration.
    provider_payload_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
