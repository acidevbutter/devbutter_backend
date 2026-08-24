import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.accounts.repository import AccountRepository
from app.accounts.router import CurrentAdminAccount
from app.admin.repository import AdminRepository
from app.admin.schemas import (
    AdminCatalogEntryRead,
    AdminCatalogListRead,
    AdminClientCounts,
    AdminClientDetailRead,
    AdminClientListItem,
    AdminClientListRead,
    AdminConversationDetailRead,
    AdminConversationDraftRead,
    AdminConversationListItem,
    AdminConversationListRead,
    AdminConversationMessageRead,
    AdminDashboardRead,
    AdminInvoiceListItem,
    AdminInvoiceListRead,
    AdminOrderListItem,
    AdminOrderListRead,
    AdminQuoteListRead,
)
from app.admin.service import AdminReadService, AdminService
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
    return AdminService(quote_service, order_service, AdminRepository(db))


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


def get_admin_read_service(db: DbSession) -> AdminReadService:
    return AdminReadService(QuoteService(QuoteRepository(db)), AdminRepository(db))


AdminReadServiceDep = Annotated[AdminReadService, Depends(get_admin_read_service)]

router = APIRouter(prefix="/admin", tags=["admin"])


def _pagination(page: int, page_size: int) -> tuple[int, int, int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    return page, page_size, (page - 1) * page_size


@router.get(
    "/quotes", response_model=list[QuoteRead], summary="List the review queue (draft quotes)"
)
async def list_review_queue(
    _admin: CurrentAdminAccount, service: AdminReadServiceDep
) -> list[QuoteRead]:
    quotes = await service.list_review_queue()
    return [QuoteRead.model_validate(q) for q in quotes]


@router.get("/quotes/all", response_model=AdminQuoteListRead)
async def list_all_quotes(
    _admin: CurrentAdminAccount,
    service: AdminReadServiceDep,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminQuoteListRead:
    page, page_size, offset = _pagination(page, page_size)
    quotes, total = await service.list_all_quotes(
        status_=status, offset=offset, limit=page_size
    )
    return AdminQuoteListRead(
        items=[QuoteRead.model_validate(quote) for quote in quotes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/clients", response_model=AdminClientListRead)
async def list_clients(
    _admin: CurrentAdminAccount,
    service: AdminReadServiceDep,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminClientListRead:
    page, page_size, offset = _pagination(page, page_size)
    rows, total = await service.list_clients(search=search, offset=offset, limit=page_size)
    return AdminClientListRead(
        items=[
            AdminClientListItem(
                id=row["account"].id,
                name=row["user"].name,
                business_name=row["company"].name,
                email=row["user"].email,
                cpf=row["user"].cpf,
                cnpj=row["company"].cnpj,
                created_at=row["account"].created_at,
                asaas_customer_id=row["asaas_customer_id"],
                quotes_count=row["quotes_count"],
                orders_count=row["orders_count"],
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/clients/{account_id}", response_model=AdminClientDetailRead)
async def get_client(
    account_id: uuid.UUID,
    _admin: CurrentAdminAccount,
    service: AdminReadServiceDep,
) -> AdminClientDetailRead:
    from app.core.exceptions import NotFoundError

    row = await service.get_client(account_id)
    if row is None:
        raise NotFoundError("Account not found")
    account = row["account"]
    company = row["company"]
    user = row["user"]
    return AdminClientDetailRead(
        id=account.id,
        name=user.name,
        business_name=company.name,
        email=user.email,
        cpf=user.cpf,
        cnpj=company.cnpj,
        phone=user.phone,
        created_at=account.created_at,
        asaas_customer_id=row["asaas_customer_id"],
        asaas_status="vinculado" if row["asaas_customer_id"] else "nao_vinculado",
        counts=AdminClientCounts(**row["counts"]),
    )


@router.get("/orders", response_model=AdminOrderListRead)
async def list_admin_orders(
    _admin: CurrentAdminAccount,
    service: AdminReadServiceDep,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminOrderListRead:
    page, page_size, offset = _pagination(page, page_size)
    rows, total = await service.list_orders(
        status_=status, search=search, offset=offset, limit=page_size
    )
    return AdminOrderListRead(
        items=[
            AdminOrderListItem(
                id=row["order"].id,
                quote_id=row["order"].quote_id,
                account_id=row["order"].account_id,
                account_name=row["account_name"],
                status=row["order"].status,
                amount=row["amount"],
                created_at=row["order"].created_at,
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/invoices", response_model=AdminInvoiceListRead)
async def list_admin_invoices(
    _admin: CurrentAdminAccount,
    service: AdminReadServiceDep,
    status: str | None = None,
    due_before: date | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminInvoiceListRead:
    page, page_size, offset = _pagination(page, page_size)
    rows, total, synced_at = await service.list_invoices(
        status_=status, due_before=due_before, offset=offset, limit=page_size
    )
    return AdminInvoiceListRead(
        items=[
            AdminInvoiceListItem(
                id=row["invoice"].id,
                order_id=row["invoice"].order_id,
                account_name=row["account_name"],
                asaas_invoice_id=row["invoice"].asaas_invoice_id,
                amount=row["invoice"].amount,
                currency=row["invoice"].currency,
                status=row["invoice"].status,
                due_date=row["invoice"].due_date,
                payment_link=row["invoice"].payment_link,
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        synced_at=synced_at,
    )


@router.get("/dashboard", response_model=AdminDashboardRead)
async def get_dashboard(
    _admin: CurrentAdminAccount, service: AdminReadServiceDep
) -> AdminDashboardRead:
    return AdminDashboardRead.model_validate(await service.dashboard())


@router.get("/catalog", response_model=AdminCatalogListRead)
async def list_admin_catalog(
    _admin: CurrentAdminAccount, service: AdminReadServiceDep
) -> AdminCatalogListRead:
    rows = await service.list_catalog()
    return AdminCatalogListRead(
        items=[
            AdminCatalogEntryRead(
                id=row["entry"].id,
                account_id=row["entry"].account_id,
                account_name=row["account_name"],
                type=row["entry"].type,
                description=row["entry"].description,
                created_at=row["entry"].created_at,
            )
            for row in rows
        ]
    )


@router.get("/conversations", response_model=AdminConversationListRead)
async def list_admin_conversations(
    _admin: CurrentAdminAccount,
    service: AdminReadServiceDep,
    has_quote: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminConversationListRead:
    page, page_size, offset = _pagination(page, page_size)
    rows, total = await service.list_conversations(
        has_quote=has_quote, offset=offset, limit=page_size
    )
    return AdminConversationListRead(
        items=[
            AdminConversationListItem(
                id=row["conversation"].id,
                account_id=row["conversation"].account_id,
                account_name=row["account_name"],
                message_count=row["message_count"],
                resulting_quote_id=row["resulting_quote_id"],
                started_at=row["conversation"].created_at,
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/conversations/{conversation_id}", response_model=AdminConversationDetailRead)
async def get_admin_conversation(
    conversation_id: uuid.UUID,
    _admin: CurrentAdminAccount,
    service: AdminReadServiceDep,
) -> AdminConversationDetailRead:
    from app.core.exceptions import NotFoundError

    row = await service.get_conversation(conversation_id)
    if row is None:
        raise NotFoundError("Conversation not found")
    conversation = row["conversation"]
    quote = row["quote"]
    messages = [
        AdminConversationMessageRead(
            role="assistant" if message.get("role") == "assistant" else "visitor",
            content=message.get("content", ""),
        )
        for message in conversation.transcript or []
    ]
    return AdminConversationDetailRead(
        id=conversation.id,
        account_id=conversation.account_id,
        account_name=row["account_name"],
        message_count=row["message_count"],
        resulting_quote_id=quote.id if quote else None,
        started_at=conversation.created_at,
        messages=messages,
        draft=(
            AdminConversationDraftRead(
                scope_summary=quote.scope_summary,
                price_range=quote.price_range,
                timeline=quote.timeline,
            )
            if quote
            else None
        ),
    )


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
