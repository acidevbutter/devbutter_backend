import hmac
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Header, Request, status
from sqlalchemy import select

from app.accounts.dependencies import CurrentAdminAccount
from app.accounts.models import Account, Company
from app.core.dependencies import DbSession
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.invoices.models import Invoice
from app.orders.models import Order
from app.settings.config import settings
from app.webhooks.dependencies import WebhookServiceDep
from app.webhooks.repository import WebhookEventRepository
from app.webhooks.schemas import (
    AdminWebhookEventDetailRead,
    AdminWebhookEventListRead,
    AdminWebhookEventRead,
    AsaasWebhookEvent,
    WebhookAttemptRead,
    WebhookCorrelationRead,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
admin_router = APIRouter(prefix="/admin/webhooks", tags=["admin-webhooks"])


@router.post(
    "/asaas",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Receive Asaas payment status change events",
)
async def receive_asaas_webhook(
    request: Request,
    payload: AsaasWebhookEvent,
    service: WebhookServiceDep,
    asaas_access_token: Annotated[
        str | None,
        Header(alias="asaas-access-token"),
    ] = None,
) -> None:
    if not settings.asaas_webhook_access_token or not hmac.compare_digest(
        (asaas_access_token or "").encode("utf-8"),
        settings.asaas_webhook_access_token.encode("utf-8"),
    ):
        # Constant-time comparison -- was `!=` (00-levantamento-tecnico.md
        # item B4 / C4, fixed as part of this slice's entregavel 5). Same
        # pattern Selolibertaria's own Mercado Pago webhook already uses.
        raise UnauthorizedError("Invalid asaas-access-token")
    raw_payload = await request.json()
    await service.process_event(payload, raw_payload)


@admin_router.get("/events", response_model=AdminWebhookEventListRead)
async def list_webhook_events(
    _admin: CurrentAdminAccount,
    db: DbSession,
    event_type: str | None = None,
    has_error: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminWebhookEventListRead:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    events, total, error_count = await WebhookEventRepository(db).list(
        event_type=event_type,
        has_error=has_error,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return AdminWebhookEventListRead(
        items=[AdminWebhookEventRead.model_validate(event) for event in events],
        total=total,
        page=page,
        page_size=page_size,
        error_count=error_count,
    )


_SENSITIVE_KEYS = (
    "customer",
    "wallet",
    "apikey",
    "accesstoken",
    "token",
    "cpfcnpj",
    "pdfurl",
    "xmlurl",
)


def _mask_payload(value: object) -> object:
    """Redact obviously-identifying values before the payload reaches the
    drawer (artboard 1r labels it "payload (redigido)")."""
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, val in value.items():
            if isinstance(val, str) and any(
                s in key.lower() for s in _SENSITIVE_KEYS
            ):
                out[key] = f"****{val[-4:]}" if len(val) > 4 else "****"
            else:
                out[key] = _mask_payload(val)
        return out
    if isinstance(value, list):
        return [_mask_payload(v) for v in value]
    return value


@admin_router.get("/events/{event_id}", response_model=AdminWebhookEventDetailRead)
async def get_webhook_event(
    event_id: uuid.UUID,
    _admin: CurrentAdminAccount,
    db: DbSession,
) -> AdminWebhookEventDetailRead:
    event = await WebhookEventRepository(db).get(event_id)
    if event is None:
        raise NotFoundError("Webhook event not found")

    # We keep a single audit row per delivery (received_at + processed_at),
    # not a per-retry log -- reconstruct the attempt line from those two.
    attempts: list[WebhookAttemptRead] = [
        WebhookAttemptRead(at=event.received_at, result="recebido")
    ]
    if event.processed_at is not None:
        attempts.append(
            WebhookAttemptRead(
                at=event.processed_at,
                result="ok" if event.processed_ok else "erro",
            )
        )

    correlation: list[WebhookCorrelationRead] = []
    if event.related_invoice_id is not None:
        row = (
            await db.execute(
                select(Invoice, Order, Company.name)
                .join(Order, Order.id == Invoice.order_id)
                .join(Account, Account.id == Order.account_id)
                .join(Company, Company.id == Account.company_id)
                .where(Invoice.id == event.related_invoice_id)
            )
        ).one_or_none()
        if row is not None:
            invoice, order, company_name = row
            correlation.append(
                WebhookCorrelationRead(kind="client", label=company_name, href=None)
            )
            correlation.append(
                WebhookCorrelationRead(
                    kind="invoice",
                    label=f"inv {str(invoice.id)[:8]}",
                    href="/admin/invoices",
                )
            )
            correlation.append(
                WebhookCorrelationRead(
                    kind="order",
                    label=f"order {str(order.id)[:8]}",
                    href=f"/admin/orders/{order.id}",
                )
            )

    return AdminWebhookEventDetailRead(
        id=event.id,
        event_type=event.event_type,
        related_invoice_id=event.related_invoice_id,
        processed_ok=event.processed_ok,
        error_message=event.error_message,
        payload=cast(dict[str, Any], _mask_payload(event.payload)),
        received_at=event.received_at,
        processed_at=event.processed_at,
        attempts=attempts,
        correlation=correlation,
    )


@admin_router.post(
    "/events/{event_id}/reprocess",
    response_model=AdminWebhookEventRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_webhook_event(
    event_id: uuid.UUID,
    _admin: CurrentAdminAccount,
    service: WebhookServiceDep,
    db: DbSession,
) -> AdminWebhookEventRead:
    audit_event = await WebhookEventRepository(db).get(event_id)
    if audit_event is None:
        raise NotFoundError("Webhook event not found")
    event = await service.reprocess_event(audit_event)
    return AdminWebhookEventRead.model_validate(event)
