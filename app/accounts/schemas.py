import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.validators.documents import is_valid_cnpj, normalize_document


class CompanyType(str, Enum):
    """Asaas subaccount ``companyType`` -- required for PJ (which is the only
    kind of subaccount DevButter opens). Mirrors
    ``asaas_payment/app/subaccounts/schemas.py``."""

    MEI = "MEI"
    LIMITED = "LIMITED"
    INDIVIDUAL = "INDIVIDUAL"
    ASSOCIATION = "ASSOCIATION"


class BillingProfileInput(BaseModel):
    """"Dados de recebimento" a PJ submits so DevButter can open its Asaas
    white-label subaccount. Field names are local (snake_case); the mapping to
    Asaas's camelCase ``POST /v3/accounts`` body lives in
    ``SubaccountProvisioningService``. Validated at the edge so a bad CNPJ or
    postal code is a 422 here, not a wasted round trip to Asaas.
    """

    legal_name: str = Field(min_length=1, max_length=255)
    billing_email: EmailStr
    cnpj: str
    company_type: CompanyType
    mobile_phone: str
    income_value: Decimal = Field(gt=0)
    address: str = Field(min_length=1, max_length=255)
    address_number: str = Field(min_length=1, max_length=32)
    complement: str | None = Field(default=None, max_length=255)
    province: str = Field(min_length=1, max_length=120)
    postal_code: str
    site: str | None = Field(default=None, max_length=255)

    @field_validator("cnpj")
    @classmethod
    def _validate_cnpj(cls, value: str) -> str:
        digits = normalize_document(value)
        if not is_valid_cnpj(digits):
            raise ValueError("cnpj must be a valid CNPJ (14 digits, valid check digits)")
        return digits

    @field_validator("mobile_phone")
    @classmethod
    def _validate_mobile_phone(cls, value: str) -> str:
        digits = normalize_document(value)
        if not 10 <= len(digits) <= 11:
            raise ValueError("mobile_phone must have 10 or 11 digits (DDD + number)")
        return digits

    @field_validator("postal_code")
    @classmethod
    def _validate_postal_code(cls, value: str) -> str:
        digits = normalize_document(value)
        if len(digits) != 8:
            raise ValueError("postal_code must have 8 digits")
        return digits


class BillingProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    legal_name: str
    billing_email: str
    cnpj: str
    company_type: CompanyType
    mobile_phone: str
    income_value: Decimal
    address: str
    address_number: str
    complement: str | None
    province: str
    postal_code: str
    site: str | None
    updated_at: datetime


class SubaccountStatusRead(BaseModel):
    """Compact status block surfaced on ``GET /accounts/me`` so the portal can
    render the onboarding gate without a second call."""

    model_config = ConfigDict(from_attributes=True)

    provisioning_status: str
    onboarding_status: str | None
    approval_status: str | None
    commercial_info_status: str | None
    bank_account_info_status: str | None
    documentation_status: str | None
    commercial_info_expires_at: datetime | None
    last_error: str | None


class AccountSignup(BaseModel):
    email: EmailStr
    password: str
    name: str
    cpf: str | None = None
    phone: str | None = None
    product_name: str | None = None
    company_name: str | None = None
    cnpj: str | None = None
    # Required for the portal PJ signup (2nd step). Optional at the schema level
    # so the visitor-onboarding path (conversation funnel) can still create an
    # account without it and collect the profile later via PUT
    # /accounts/billing-profile.
    billing_profile: BillingProfileInput | None = None


class AccountUpdate(BaseModel):
    name: str | None = None
    cpf: str | None = None
    phone: str | None = None
    product_name: str | None = None
    company_name: str | None = None
    cnpj: str | None = None


class AccountRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    name: str
    cpf: str | None
    phone: str | None
    product_name: str
    company_id: uuid.UUID
    company_name: str
    cnpj: str | None
    is_admin: bool
    is_active: bool
    created_at: datetime
    accounts: list["AccountSelectionRead"]
    # True once the company's subaccount has been provisioned but not yet
    # approved by Asaas -- the portal blocks everything but /portal/recebimentos
    # while this is set (see docs/design/portal-recebimentos-onboarding.md).
    onboarding_required: bool = False
    subaccount_status: SubaccountStatusRead | None = None


class AccountSelectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_name: str
    company_id: uuid.UUID
    company_name: str
    cnpj: str | None
    created_at: datetime


class AccountCreate(BaseModel):
    product_name: str
    company_id: uuid.UUID | None = None


class CompanyCreate(BaseModel):
    name: str
    cnpj: str | None = None


class CompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    cnpj: str | None
    created_at: datetime


class AccountSelect(BaseModel):
    account_id: uuid.UUID


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class PasswordSetupCheck(BaseModel):
    email: str


class PasswordSetupComplete(BaseModel):
    token: str
    password: str


class PasswordRecoveryRequest(BaseModel):
    email: EmailStr


class PasswordRecoveryRequestAccepted(BaseModel):
    message: str = "Se o e-mail estiver cadastrado, enviaremos instruções."


class PasswordRecoveryCheck(BaseModel):
    valid: bool = True


class PasswordRecoveryComplete(BaseModel):
    token: str
    password: str
