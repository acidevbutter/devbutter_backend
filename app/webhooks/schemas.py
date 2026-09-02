import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AsaasWebhookPayment(BaseModel):
    id: str
    status: str


class AsaasWebhookEvent(BaseModel):
    """Asaas's webhook body. Two shapes share this endpoint:
    {"event": "PAYMENT_*", "payment": {...}} (reconciled against
    Invoice/Order) and {"event": "ACCOUNT_STATUS_*", "account": {...}}
    (00-levantamento-tecnico.md item B5) -- accepted, audited, and otherwise
    a no-op, see app/webhooks/service.py. `payment`/`account` are optional so
    one event type validates without the other's field; `extra="allow"` on
    the model is deliberate too -- 00-levantamento-tecnico.md item B7: "o
    payload pode ganhar campos novos" and this consumer must not break on
    them.
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


class WebhookAttemptRead(BaseModel):
    at: datetime
    result: str  # "ok" | "erro" | "recebido"


class WebhookCorrelationRead(BaseModel):
    kind: str  # "client" | "invoice" | "order"
    label: str
    href: str | None = None


class AdminWebhookEventDetailRead(AdminWebhookEventRead):
    """Drawer view (artboard 1r): the audit row plus the entities it touched
    and the processing attempts we can reconstruct from its timestamps."""

    processed_at: datetime | None = None
    attempts: list[WebhookAttemptRead] = []
    correlation: list[WebhookCorrelationRead] = []
