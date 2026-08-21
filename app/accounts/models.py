import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    document: Mapped[str | None] = mapped_column(String(20), default=None)
    phone: Mapped[str | None] = mapped_column(String(30), default=None)
    business_name: Mapped[str | None] = mapped_column(String(200), default=None)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AsaasCustomerLink(Base):
    """The Asaas customer id created for an Account's Layer A billing --
    created lazily at order approval (see orders/service.py), not at signup,
    so no Asaas record is created for visitors who never convert. One Account
    maps to at most one Asaas Customer (`accounts/{id}` is not the same thing
    as a Layer B merchant subaccount -- see architecture doc §4).
    """

    __tablename__ = "asaas_customer_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, index=True
    )
    asaas_customer_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
