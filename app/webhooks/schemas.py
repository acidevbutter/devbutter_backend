import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AsaasWebhookPayment(BaseModel):
    id: str
    status: str


class AsaasWebhookEvent(BaseModel):
    """Asaas's webhook body. Two shapes share this endpoint:
    {"event": "PAYMENT_*", "payment": {...}} (the original, still the only
    one this service reconciles against Invoice/Order) and
    {"event": "ACCOUNT_STATUS_*", "account": {...}} (00-levantamento-tecnico.md
    item B5) -- routed to CredentialRotationService instead, see
    app/webhooks/service.py. `payment`/`account` are optional so one event
    type validates without the other's field; `extra="allow"` on the model
    is deliberate too -- 00-levantamento-tecnico.md item B7: "o payload pode
    ganhar campos novos" and this consumer must not break on them. The exact
    shape of `account` is not confirmed against real Asaas payloads (kept as
    a loose dict rather than a typed model for that reason) -- only that it
    carries the subaccount's `id` under the key `id`, mirroring `payment`.
    """

    model_config = ConfigDict(extra="allow")

    event: str
    payment: AsaasWebhookPayment | None = None
    account: dict[str, Any] | None = None


class AdminWebhookEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    related_invoice_id: uuid.UUID | None
    processed_ok: bool
    error_message: str | None
    payload: dict[str, Any]
    received_at: datetime


class AdminWebhookEventListRead(BaseModel):
    items: list[AdminWebhookEventRead]
    total: int
    page: int
    page_size: int
    error_count: int
