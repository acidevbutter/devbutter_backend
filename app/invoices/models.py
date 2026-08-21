import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

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
    asaas_invoice_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    due_date: Mapped[date] = mapped_column(Date)
    payment_link: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
