import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaymentSecurityContext(Base):
    __tablename__ = "payment_security_contexts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    context_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"))
    operation: Mapped[str] = mapped_column(String(24))
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(16), default="unused", index=True)
    request_id: Mapped[str | None] = mapped_column(String(36), unique=True, default=None)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    asaas_customer_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="asaas")
    provider_token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    token_kid: Mapped[str] = mapped_column(String(64))
    brand: Mapped[str] = mapped_column(String(24))
    last4: Mapped[str] = mapped_column(String(4))
    expiry_month: Mapped[int] = mapped_column()
    expiry_year: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("invoice_id", "idempotency_key", name="uq_payment_attempt_invoice_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    payment_method_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment_methods.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16), default="processing", index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(64), default=None)
    failure_code: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
