import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class OrderApprovalRequest(BaseModel):
    """Marcos supplies the exact billable amount and due date at approval
    time -- Quote.price_range is a human-readable range (e.g. "R$5k-R$8k"),
    not something safe to parse into a charge automatically.
    """

    amount: Decimal
    due_date: date
    billing_type: str = "UNDEFINED"  # Asaas billingType: BOLETO | CREDIT_CARD | PIX | UNDEFINED


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    quote_id: uuid.UUID
    account_id: uuid.UUID
    status: str
    created_at: datetime
