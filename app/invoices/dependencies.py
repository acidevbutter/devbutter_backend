from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.invoices.repository import InvoiceRepository
from app.invoices.service import InvoiceService


def get_invoice_service(db: DbSession) -> InvoiceService:
    return InvoiceService(InvoiceRepository(db))


InvoiceServiceDep = Annotated[InvoiceService, Depends(get_invoice_service)]


def get_invoice_repository(db: DbSession) -> InvoiceRepository:
    return InvoiceRepository(db)


InvoiceRepositoryDep = Annotated[InvoiceRepository, Depends(get_invoice_repository)]
