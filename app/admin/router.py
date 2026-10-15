import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, status

from app.accounts.dependencies import CurrentAdminAccount
from app.admin.dependencies import (
    AdminMindServiceDep,
    AdminReadServiceDep,
    AdminServiceDep,
)
from app.admin.schemas import (
    AdminCatalogEntryRead,
    AdminCatalogListRead,
    AdminClientCounts,
    AdminClientDetailRead,
    AdminClientDocumentUpdate,
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
    AdminLLMBudgetRead,
    AdminLLMBudgetUpdate,
    AdminLLMUsageSummaryRead,
    AdminMindDashboardRead,
    AdminMindGovernanceRead,
    AdminMindGovernanceUpdate,
    AdminMindTurnMetricRead,
    AdminOrderListItem,
    AdminOrderListRead,
    AdminQuoteListRead,
)
from app.orders.schemas import OrderApprovalRequest, OrderRead
from app.quotes.schemas import QuoteEdit, QuoteRead

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/mind/overview", response_model=AdminMindDashboardRead)
async def get_mind_dashboard(
    _admin: CurrentAdminAccount, service: AdminMindServiceDep
) -> AdminMindDashboardRead:
    return AdminMindDashboardRead.model_validate(await service.dashboard_overview())


@router.get("/mind/settings", response_model=AdminMindGovernanceRead)
async def get_mind_settings(
    _admin: CurrentAdminAccount, service: AdminMindServiceDep
) -> AdminMindGovernanceRead:
    return AdminMindGovernanceRead.model_validate(await service.governance())


@router.patch("/mind/settings", response_model=AdminMindGovernanceRead)
async def update_mind_settings(
    payload: AdminMindGovernanceUpdate,
    _admin: CurrentAdminAccount,
    service: AdminMindServiceDep,
) -> AdminMindGovernanceRead:
    return AdminMindGovernanceRead.model_validate(
        await service.update_governance(payload.model_dump())
    )


@router.get("/mind/usage", response_model=AdminLLMUsageSummaryRead)
async def get_mind_usage(
    _admin: CurrentAdminAccount, service: AdminMindServiceDep
) -> AdminLLMUsageSummaryRead:
    return AdminLLMUsageSummaryRead.model_validate(await service.llm_usage_summary())


@router.get("/mind/budgets/{flow}", response_model=AdminLLMBudgetRead | None)
async def get_mind_budget(
    flow: str, _admin: CurrentAdminAccount, service: AdminMindServiceDep
) -> AdminLLMBudgetRead | None:
    budget = await service.llm_budget(flow)
    return AdminLLMBudgetRead.model_validate(budget) if budget else None


@router.put("/mind/budgets/{flow}", response_model=AdminLLMBudgetRead)
async def update_mind_budget(
    flow: str,
    payload: AdminLLMBudgetUpdate,
    _admin: CurrentAdminAccount,
    service: AdminMindServiceDep,
) -> AdminLLMBudgetRead:
    return AdminLLMBudgetRead.model_validate(
        await service.update_llm_budget(flow, payload.model_dump())
    )


@router.get(
    "/mind/conversations/{conversation_id}/turn-metrics",
    response_model=list[AdminMindTurnMetricRead],
)
async def get_mind_conversation_turn_metrics(
    conversation_id: uuid.UUID,
    _admin: CurrentAdminAccount,
    service: AdminMindServiceDep,
) -> list[AdminMindTurnMetricRead]:
    return [
        AdminMindTurnMetricRead.model_validate(item)
        for item in await service.conversation_turn_metrics(conversation_id)
    ]


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
    quotes, total = await service.list_all_quotes(status_=status, offset=offset, limit=page_size)
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


def _client_detail(row: dict[str, Any]) -> AdminClientDetailRead:
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
        subaccount_onboarding=row.get("subaccount_onboarding"),
        subaccount_approval=row.get("subaccount_approval"),
        counts=AdminClientCounts(**row["counts"]),
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
    return _client_detail(row)


@router.patch(
    "/clients/{account_id}/document",
    response_model=AdminClientDetailRead,
    summary="Fill the client's CPF/CNPJ (required by Asaas before approving a quote)",
)
async def update_client_document(
    account_id: uuid.UUID,
    payload: AdminClientDocumentUpdate,
    _admin: CurrentAdminAccount,
    service: AdminServiceDep,
) -> AdminClientDetailRead:
    row = await service.update_client_document(account_id, cpf=payload.cpf, cnpj=payload.cnpj)
    return _client_detail(row)


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
                invoices_total=row.get("invoices_total", 0),
                invoices_paid=row.get("invoices_paid", 0),
                invoices_overdue=row.get("invoices_overdue", 0),
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


@router.get(
    "/quotes/{quote_id}",
    response_model=QuoteRead,
    summary="Quote detail for review, with the AI price_range (never redacted)",
)
async def get_admin_quote(
    quote_id: uuid.UUID, _admin: CurrentAdminAccount, service: AdminReadServiceDep
) -> QuoteRead:
    return QuoteRead.model_validate(await service.get_quote(quote_id))


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
