import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class AccountSignup(BaseModel):
    email: EmailStr
    password: str
    name: str
    cpf: str | None = None
    phone: str | None = None
    product_name: str | None = None
    company_name: str | None = None
    cnpj: str | None = None


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
