import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request, status

from app.accounts.dependencies import CurrentAdminAccount
from app.core.dependencies import DbSession
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.settings.config import settings
from app.webhooks.dependencies import WebhookServiceDep
from app.webhooks.repository import WebhookEventRepository
from app.webhooks.schemas import AdminWebhookEventListRead, AdminWebhookEventRead, AsaasWebhookEvent

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
