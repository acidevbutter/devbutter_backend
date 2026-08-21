import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.invoices.models import Invoice


class InvoiceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, invoice_id: uuid.UUID) -> Invoice | None:
        return await self.session.get(Invoice, invoice_id)

    async def get_by_asaas_invoice_id(self, asaas_invoice_id: str) -> Invoice | None:
        result = await self.session.execute(
            select(Invoice).where(Invoice.asaas_invoice_id == asaas_invoice_id)
        )
        return result.scalar_one_or_none()

    async def list_by_order(self, order_id: uuid.UUID) -> list[Invoice]:
        result = await self.session.execute(
            select(Invoice).where(Invoice.order_id == order_id).order_by(Invoice.created_at.desc())
        )
        return list(result.scalars())

    async def create(
        self,
        *,
        order_id: uuid.UUID,
        asaas_invoice_id: str,
        amount: Decimal,
        due_date: date,
        status: str,
        payment_link: str | None,
    ) -> Invoice:
        invoice = Invoice(
            order_id=order_id,
            asaas_invoice_id=asaas_invoice_id,
            amount=amount,
            due_date=due_date,
            status=status,
            payment_link=payment_link,
        )
        self.session.add(invoice)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"An invoice already exists for Asaas id {asaas_invoice_id!r}"
            ) from exc
        await self.session.refresh(invoice)
        return invoice

    async def update_status(self, invoice: Invoice, status_: str) -> tuple[Invoice, bool]:
        """Idempotent: returns (invoice, changed). Re-delivering the same
        webhook event with a status that's already applied is a no-op --
        callers use `changed` to decide whether to fire a client notification.
        """
        if invoice.status == status_:
            return invoice, False
        invoice.status = status_
        await self.session.commit()
        await self.session.refresh(invoice)
        return invoice, True
