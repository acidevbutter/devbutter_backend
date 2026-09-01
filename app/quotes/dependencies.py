from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.quotes.repository import QuoteRepository
from app.quotes.service import QuoteService


def get_quote_service(db: DbSession) -> QuoteService:
    return QuoteService(QuoteRepository(db))


QuoteServiceDep = Annotated[QuoteService, Depends(get_quote_service)]
