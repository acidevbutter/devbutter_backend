import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.accounts.router import CurrentAccount, CurrentAdminAccount, CurrentUser
from app.core.dependencies import DbSession
from app.core.exceptions import NotFoundError
from app.invoices.repository import InvoiceRepository
from app.orders.repository import OrderRepository
from app.quotes.models import Quote
from app.quotes.repository import QuoteRepository
from app.quotes.schemas import QuoteManualCreate, QuoteRead
from app.quotes.service import QuoteService


def get_quote_service(db: DbSession) -> QuoteService:
    return QuoteService(QuoteRepository(db))


QuoteServiceDep = Annotated[QuoteService, Depends(get_quote_service)]


def get_order_repository(db: DbSession) -> OrderRepository:
    return OrderRepository(db)


OrderRepositoryDep = Annotated[OrderRepository, Depends(get_order_repository)]


def get_invoice_repository(db: DbSession) -> InvoiceRepository:
    return InvoiceRepository(db)


InvoiceRepositoryDep = Annotated[InvoiceRepository, Depends(get_invoice_repository)]

router = APIRouter(prefix="/quotes", tags=["quotes"])


async def _client_facing_quote_read(
    quote: Quote, order_repository: OrderRepository, invoice_repository: InvoiceRepository
) -> QuoteRead:
    """Redacts price_range for the two client-facing routes below -- the
    AI's draft price must not reach the client before Marcos reviews it (see
    docs/plano-preco-so-apos-aprovacao.md). /admin/quotes* is untouched by
    this and keeps building QuoteRead straight from the model, no redaction,
    for every status: that's Marcos reviewing, seeing the draft is the point.

    Once a quote is approved, the value shown is the actually-approved
    Invoice.amount (Marcos may have edited it at approval time), not the
    AI's original price_range guess -- they can legitimately differ.
    """
    quote_read = QuoteRead.model_validate(quote)
    if quote.status != "approved":
        return quote_read.model_copy(update={"price_range": None})

    order = await order_repository.get_by_quote(quote.id)
    invoices = await invoice_repository.list_by_order(order.id) if order is not None else []
    approved_amount = str(invoices[0].amount) if invoices else None
    return quote_read.model_copy(update={"price_range": approved_amount})


@router.get("", response_model=list[QuoteRead], summary="List the authenticated account's quotes")
async def list_my_quotes(
    account: CurrentAccount,
    service: QuoteServiceDep,
    order_repository: OrderRepositoryDep,
    invoice_repository: InvoiceRepositoryDep,
) -> list[QuoteRead]:
    quotes = await service.list_for_account(account.id)
    return [
        await _client_facing_quote_read(q, order_repository, invoice_repository) for q in quotes
    ]


@router.get("/{quote_id}", response_model=QuoteRead, summary="Get a quote")
async def get_quote(
    quote_id: uuid.UUID,
    account: CurrentAccount,
    user: CurrentUser,
    service: QuoteServiceDep,
    order_repository: OrderRepositoryDep,
    invoice_repository: InvoiceRepositoryDep,
) -> QuoteRead:
    quote = await service.get(quote_id)
    if quote.account_id != account.id and not user.is_admin:
        raise NotFoundError("Quote not found")
    return await _client_facing_quote_read(quote, order_repository, invoice_repository)


@router.post(
    "/manual",
    response_model=QuoteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Marcos sets a quote directly for a client (source=manual)",
)
async def create_manual_quote(
    payload: QuoteManualCreate, _admin: CurrentAdminAccount, service: QuoteServiceDep
) -> QuoteRead:
    quote = await service.create_manual(payload)
    return QuoteRead.model_validate(quote)
