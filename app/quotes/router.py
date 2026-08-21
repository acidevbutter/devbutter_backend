import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.accounts.router import CurrentAccount, CurrentAdminAccount
from app.core.dependencies import DbSession
from app.core.exceptions import NotFoundError
from app.quotes.repository import QuoteRepository
from app.quotes.schemas import QuoteManualCreate, QuoteRead
from app.quotes.service import QuoteService


def get_quote_service(db: DbSession) -> QuoteService:
    return QuoteService(QuoteRepository(db))


QuoteServiceDep = Annotated[QuoteService, Depends(get_quote_service)]

router = APIRouter(prefix="/quotes", tags=["quotes"])


@router.get("", response_model=list[QuoteRead], summary="List the authenticated account's quotes")
async def list_my_quotes(account: CurrentAccount, service: QuoteServiceDep) -> list[QuoteRead]:
    quotes = await service.list_for_account(account.id)
    return [QuoteRead.model_validate(q) for q in quotes]


@router.get("/{quote_id}", response_model=QuoteRead, summary="Get a quote")
async def get_quote(
    quote_id: uuid.UUID, account: CurrentAccount, service: QuoteServiceDep
) -> QuoteRead:
    quote = await service.get(quote_id)
    if quote.account_id != account.id and not account.is_admin:
        raise NotFoundError("Quote not found")
    return QuoteRead.model_validate(quote)


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
