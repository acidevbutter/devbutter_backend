import uuid

from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError
from app.invoices.models import Invoice
from app.invoices.repository import InvoiceRepository


@log_errors
class InvoiceService:
    def __init__(self, repository: InvoiceRepository):
        self.repository = repository

    async def get(self, invoice_id: uuid.UUID) -> Invoice:
        invoice = await self.repository.get(invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice not found")
        return invoice

    async def list_for_order(self, order_id: uuid.UUID) -> list[Invoice]:
        return await self.repository.list_by_order(order_id)
