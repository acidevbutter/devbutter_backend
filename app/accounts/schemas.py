import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class AccountSignup(BaseModel):
    email: EmailStr
    password: str
    name: str
    document: str | None = None
    phone: str | None = None
    business_name: str | None = None


class AccountUpdate(BaseModel):
    name: str | None = None
    document: str | None = None
    phone: str | None = None
    business_name: str | None = None


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    document: str | None
    phone: str | None
    business_name: str | None
    is_admin: bool
    is_active: bool
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
