import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AsaasWebhookPayment(BaseModel):
    id: str
    status: str


class AsaasWebhookEvent(BaseModel):
    """Asaas's webhook body: {"event": "PAYMENT_RECEIVED", "payment": {...}}.
    Only the fields this service actually consumes are typed; everything else
    in the payload is ignored. See https://docs.asaas.com/docs/duvidas-frequentes-webhooks.
    """

    event: str
    payment: AsaasWebhookPayment


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
