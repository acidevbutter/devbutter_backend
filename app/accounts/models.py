import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    """Identity, credentials and personal data shared by all user accounts."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # Nullable: a User created by AccountService.get_or_create_from_extraction
    # (visitor onboarding via AI conversation, see
    # docs/plano-onboarding-conversa-primeiro.md) has no password yet --
    # auth_method="magic_link" until they set one from the dashboard.
    password_hash: Mapped[str | None] = mapped_column(String(200), default=None)
    auth_method: Mapped[str] = mapped_column(String(20), default="password")  # password|magic_link
    name: Mapped[str] = mapped_column(String(200))
    cpf: Mapped[str | None] = mapped_column(String(20), default=None)
    phone: Mapped[str | None] = mapped_column(String(30), default=None)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    companies: Mapped[list["Company"]] = relationship(back_populates="user")


class Company(Base):
    """Fiscal company owned by a user and shared by one or more products."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str | None] = mapped_column(String(20), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    user: Mapped[User] = relationship(back_populates="companies")
    accounts: Mapped[list["Account"]] = relationship(back_populates="company")


class Account(Base):
    """A product context owned by a company.

    Quotes, orders, invoices and catalog entries remain account-scoped, while
    credentials and personal data live exclusively on ``User``.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    product_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    company: Mapped[Company] = relationship(back_populates="accounts")


class AsaasCustomerLink(Base):
    """The Asaas customer id created for a Company's Layer A billing --
    created lazily at order approval (see orders/service.py), not at signup,
    so no Asaas record is created for visitors who never convert. One Company
    maps to at most one Asaas Customer (`companies/{id}` is not the same thing
    as a Layer B merchant subaccount -- see architecture doc §4).
    """

    __tablename__ = "asaas_customer_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), unique=True, index=True
    )
    asaas_customer_id: Mapped[str] = mapped_column(String(64))
    external_reference: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AsaasSubaccountLink(Base):
    """The Layer B merchant subaccount (asaas_payment's white-label
    /subaccounts resource) linked to a Company -- distinct from
    AsaasCustomerLink, which is Layer A's billing-of-DevButter's-own-client
    concept. One Company has at most one payment method configured this way
    (see docs/plano-admin-empresas-cota-split.md §2, decision 4: cota and
    payment method are scoped per Company, not per Account, since a company
    with multiple product Accounts still has one meio de pagamento). The
    quota fields (split_quota_percent and its authorship) are folded in
    here from the start, per docs/plano-admin-empresas-cota-split.md §3, to
    avoid a second migration on the same table.
    """

    __tablename__ = "asaas_subaccount_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), unique=True, index=True
    )
    # Nullable: a row is written even when the Asaas create call failed
    # (provisioning_status="CREATION_FAILED"), so the admin panel can show the
    # failure and retry without losing the company <-> attempt association.
    asaas_account_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    wallet_id: Mapped[str | None] = mapped_column(String(64), default=None)
    # NONE (row exists only to hold a failure) | PROVISIONED | CREATION_FAILED | CLOSED
    provisioning_status: Mapped[str] = mapped_column(
        String(32), default="NONE", server_default="NONE"
    )
    last_error: Mapped[str | None] = mapped_column(default=None)
    onboarding_status: Mapped[str | None] = mapped_column(String(32), default=None)
    approval_status: Mapped[str | None] = mapped_column(String(32), default=None)
    # Per-dimension cadastral status mirrored from ACCOUNT_STATUS_* webhooks
    # (asaas_subcontas_fluxo_completo.md §11 / §29).
    commercial_info_status: Mapped[str | None] = mapped_column(String(32), default=None)
    bank_account_info_status: Mapped[str | None] = mapped_column(String(32), default=None)
    documentation_status: Mapped[str | None] = mapped_column(String(32), default=None)
    commercial_info_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    split_quota_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), default=None
    )  # nullable teto of active PERCENTAGE split rules' sum; null = no extra cap
    # References accounts.id, not users.id -- same "which admin did this"
    # convention as Quote.reviewed_by (CurrentAdminAccount resolves to an
    # Account, not a User).
    quota_set_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), default=None
    )
    quota_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PasswordSetupToken(Base):
    """A one-time link letting a passwordless User (auth_method="magic_link",
    see User.password_hash) set a real password -- issued by
    AccountService.issue_password_setup_token when a visitor's chat creates
    an account without typing a password inline (Fluxo Cotação IA canvas
    screen 1e), emailed via NotificationService.notify_quote_received.
    Only `token_hash` is stored (sha256 of the opaque token), same
    never-store-the-raw-secret rule as password_hash itself -- the raw token
    is returned to the caller once, at issuance, and never persisted.
    """

    __tablename__ = "password_setup_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PasswordRecoveryToken(Base):
    """One-time token for an existing portal user to replace a password.

    This deliberately has its own table: a recovery token must never be
    accepted by the onboarding-only password-setup endpoints.
    """

    __tablename__ = "password_recovery_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyFeature(Base):
    """A feature toggled on for a Company whose effect provisions a
    resource on asaas_payment -- e.g. "sells_via_api" provisions an
    AccessToken. FeatureService (app/admin) is the only writer; this table
    only tracks status and a reference to what was provisioned, never a
    secret itself (asaas_access_token_id is the id asaas_payment/Asaas
    returned, not the token value -- same non-reexposure rule as apiKey).
    See docs/plano-feature-venda-via-api.md §3.2.
    """

    __tablename__ = "company_features"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    feature_code: Mapped[str] = mapped_column(String(64))  # "sells_via_api" (extensible)
    # pending|active|error|disabled
    status: Mapped[str] = mapped_column(String(20), default="pending")
    asaas_access_token_id: Mapped[str | None] = mapped_column(String(64), default=None)
    # References accounts.id, not users.id -- see AsaasSubaccountLink.quota_set_by.
    enabled_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), default=None
    )
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CompanyBillingProfile(Base):
    """The "dados de recebimento" a Company submits at signup, needed to open
    its Asaas white-label subaccount (Layer B). One row per Company. This is
    the source payload for SubaccountProvisioningService: field names map 1:1
    onto Asaas's ``POST /v3/accounts`` body (see
    docs/asaas/asaas_subcontas_fluxo_completo.md §4.1). PJ only -- ``cnpj`` and
    ``company_type`` are always required; there is no CPF/birthDate path.

    The subaccount's credential is never stored here (or anywhere in this repo)
    -- it lives encrypted in asaas_payment. This table is re-read later for the
    annual ``commercialInfo`` reconfirmation, so it must stay complete and
    current even after the subaccount is approved.
    """

    __tablename__ = "company_billing_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), unique=True, index=True
    )
    legal_name: Mapped[str] = mapped_column(String(255))  # -> name (razão social)
    billing_email: Mapped[str] = mapped_column(String(320))  # -> email
    cnpj: Mapped[str] = mapped_column(String(14))  # -> cpfCnpj, digits only
    company_type: Mapped[str] = mapped_column(String(20))  # MEI|LIMITED|INDIVIDUAL|ASSOCIATION
    mobile_phone: Mapped[str] = mapped_column(String(20))  # -> mobilePhone, digits only
    income_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # -> incomeValue
    address: Mapped[str] = mapped_column(String(255))
    address_number: Mapped[str] = mapped_column(String(32))  # -> addressNumber
    complement: Mapped[str | None] = mapped_column(String(255), default=None)
    province: Mapped[str] = mapped_column(String(120))  # bairro
    postal_code: Mapped[str] = mapped_column(String(8))  # -> postalCode, digits only
    site: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
