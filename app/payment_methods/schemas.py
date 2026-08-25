import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PaymentMethodOperation = Literal["save_only", "save_and_pay"]


class PaymentMethodSessionCreate(BaseModel):
    invoice_id: uuid.UUID
    operation: PaymentMethodOperation


class PaymentMethodSessionRead(BaseModel):
    context_id: str
    nonce: str
    expires_at: datetime
    purpose: Literal["payment_method.create"] = "payment_method.create"
    method: Literal["POST"] = "POST"
    path: Literal["/payment-methods"] = "/payment-methods"


class PaymentMethodCreate(BaseModel):
    invoice_id: uuid.UUID
    operation: PaymentMethodOperation
    envelope: dict[str, Any]


class PaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand: str
    last4: str
    expiry_month: int
    expiry_year: int
    status: str
    created_at: datetime
    revoked_at: datetime | None


class PaymentCreate(BaseModel):
    invoice_id: uuid.UUID
    payment_method_id: uuid.UUID
    idempotency_key: str = Field(min_length=16, max_length=128)


class PaymentRead(BaseModel):
    id: uuid.UUID
    state: str
    provider_payment_id: str | None


class _CardInput(BaseModel):
    holder_name: str = Field(min_length=1, max_length=120)
    number: str = Field(min_length=12, max_length=23)
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2024, le=2100)
    ccv: str = Field(min_length=3, max_length=4)


class _HolderInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    cpf_cnpj: str = Field(min_length=11, max_length=18)
    postal_code: str = Field(min_length=8, max_length=10)
    address_number: str = Field(min_length=1, max_length=20)
    phone: str | None = Field(default=None, max_length=20)
    mobile_phone: str | None = Field(default=None, max_length=20)


class CardPayload(BaseModel):
    card: _CardInput
    holder: _HolderInput
    remote_ip: str = Field(min_length=3, max_length=45)
