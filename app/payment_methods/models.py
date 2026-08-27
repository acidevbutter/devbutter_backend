import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaymentSecurityContext(Base):
    """Anti-replay ledger row for one envelope-protocol operation.

    Schema and field names follow
    docs/prompts/payment-tokenization/runs/07-contract-freeze.md section 4
    literally. `account_id`/`company_id` are this codebase's existing names
    for the contract's `subject_id`/`tenant_id` -- kept as-is rather than
    renamed, since they are real foreign keys into `accounts`/`companies`
    used the same way everywhere else in this backend.
    """

    __tablename__ = "payment_security_contexts"
    __table_args__ = (
        UniqueConstraint("kid", "nonce", name="uq_payment_security_contexts_kid_nonce"),
        UniqueConstraint(
            "account_id",
            "request_id_used",
            name="uq_payment_security_contexts_account_request",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    context_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(80))
    kid: Mapped[str] = mapped_column(String(64))
    allowed_method: Mapped[str] = mapped_column(String(8), default="POST")
    allowed_path: Mapped[str] = mapped_column(String(120), default="/payment-methods")
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), default=None
    )
    amount_minor: Mapped[int | None] = mapped_column(Integer(), default=None)
    currency: Mapped[str | None] = mapped_column(String(3), default=None)
    request_id_used: Mapped[str | None] = mapped_column(String(36), default=None)
    state: Mapped[str] = mapped_column(String(16), default="unused", index=True)
    failure_reason_code: Mapped[str | None] = mapped_column(String(64), default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
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
