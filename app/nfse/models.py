import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NfseIssuance(Base):
    """Durable outbox for one NFS-e per Layer A charge.

    Distinct from ``invoices`` (Asaas cobrança). ``asaas_nfse_id`` is the
    Asaas fiscal invoice id. Selo should keep an equivalent table keyed by
    its own order/payment id and the same asaas_account_id + asaas_nfse_id.
    """

    __tablename__ = "nfse_issuances"
    __table_args__ = (
        UniqueConstraint("invoice_id", name="uq_nfse_issuances_invoice_id"),
        UniqueConstraint("asaas_nfse_id", name="uq_nfse_issuances_asaas_nfse_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    asaas_account_id: Mapped[str] = mapped_column(String(64), index=True)
    asaas_payment_id: Mapped[str] = mapped_column(String(64), index=True)
    asaas_nfse_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    external_reference: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    nfse_number: Mapped[str | None] = mapped_column(String(64), default=None)
    validation_code: Mapped[str | None] = mapped_column(String(64), default=None)
    pdf_blob_id: Mapped[str | None] = mapped_column(String(64), default=None)
    xml_blob_id: Mapped[str | None] = mapped_column(String(64), default=None)
    document_url: Mapped[str | None] = mapped_column(String(500), default=None)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
