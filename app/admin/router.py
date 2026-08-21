import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.accounts.repository import AccountRepository
from app.accounts.router import CurrentAdminAccount
from app.admin.service import AdminService
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.clients.relay_client import RelayClient, get_relay_client
from app.core.dependencies import DbSession
from app.invoices.repository import InvoiceRepository
from app.notifications.service import NotificationService
from app.orders.repository import OrderRepository
from app.orders.schemas import OrderApprovalRequest, OrderRead
from app.orders.service import OrderService
from app.quotes.repository import QuoteRepository
from app.quotes.schemas import QuoteEdit, QuoteRead
from app.quotes.service import QuoteService


def get_admin_service(
    db: DbSession,
    asaas_client: Annotated[AsaasClient, Depends(get_asaas_client)],
    relay_client: Annotated[RelayClient, Depends(get_relay_client)],
) -> AdminService:
    quote_service = QuoteService(QuoteRepository(db))
    notification_service = NotificationService(relay_client)
    order_service = OrderService(
        OrderRepository(db),
        AccountRepository(db),
        InvoiceRepository(db),
        asaas_client,
        notification_service,
    )
    return AdminService(quote_service, order_service)


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/quotes", response_model=list[QuoteRead], summary="List the review queue (draft quotes)"
)
async def list_review_queue(
    _admin: CurrentAdminAccount, service: AdminServiceDep
) -> list[QuoteRead]:
    quotes = await service.list_review_queue()
    return [QuoteRead.model_validate(q) for q in quotes]


@router.patch(
    "/quotes/{quote_id}", response_model=QuoteRead, summary="Edit a draft quote's scope/price"
)
async def edit_quote(
    quote_id: uuid.UUID, payload: QuoteEdit, _admin: CurrentAdminAccount, service: AdminServiceDep
) -> QuoteRead:
    quote = await service.edit_quote(quote_id, payload)
    return QuoteRead.model_validate(quote)


@router.post("/quotes/{quote_id}/reject", response_model=QuoteRead, summary="Reject a draft quote")
async def reject_quote(
    quote_id: uuid.UUID, admin: CurrentAdminAccount, service: AdminServiceDep
) -> QuoteRead:
    quote = await service.reject_quote(quote_id, admin.id)
    return QuoteRead.model_validate(quote)


@router.post(
    "/quotes/{quote_id}/approve",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Approve a quote: creates the Order, Asaas Customer (if needed), and Invoice",
)
async def approve_quote(
    quote_id: uuid.UUID,
    payload: OrderApprovalRequest,
    admin: CurrentAdminAccount,
    service: AdminServiceDep,
) -> OrderRead:
    _, order = await service.approve_quote(
        quote_id,
        admin.id,
        amount=payload.amount,
        due_date=payload.due_date,
        billing_type=payload.billing_type,
    )
    return OrderRead.model_validate(order)
