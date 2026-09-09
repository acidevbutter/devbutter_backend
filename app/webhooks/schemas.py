import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AsaasWebhookPayment(BaseModel):
    id: str
    status: str


class AsaasWebhookInvoice(BaseModel):
    """Asaas NFS-e object on INVOICE_* events. Distinct from Layer A cobrança."""

    model_config = ConfigDict(extra="allow")

    id: str
    status: str | None = None
    number: str | None = None
    payment: str | None = None
    pdfUrl: str | None = None
    xmlUrl: str | None = None
    validationCode: str | None = None


class AsaasWebhookEvent(BaseModel):
    """Asaas's webhook body. Three shapes share this endpoint:
    {"event": "PAYMENT_*", "payment": {...}} (Layer A charge),
    {"event": "INVOICE_*", "invoice": {...}} (NFS-e; Option B, not via asaas_payment),
    {"event": "ACCOUNT_STATUS_*", "account": {...}} (audited no-op).
    """

    model_config = ConfigDict(extra="allow")

    # Emitter-assigned event id -- used as the idempotency key ("at least
    # once" delivery, asaas_subcontas_fluxo_completo.md §12.1). Optional: not
    # every historical/simulated delivery carries one.
    id: str | None = None
    event: str
    payment: AsaasWebhookPayment | None = None
    invoice: AsaasWebhookInvoice | None = None
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
