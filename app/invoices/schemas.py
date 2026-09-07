import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    asaas_invoice_id: str
    asaas_charge_id: str
    amount: Decimal
    currency: str
    status: str
    due_date: date
    payment_link: str | None
    last_event_at: datetime | None
    created_at: datetime
