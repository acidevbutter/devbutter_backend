import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.quotes.schemas import QuoteRead


class AdminClientListItem(BaseModel):
    id: uuid.UUID
    name: str
    business_name: str | None
    email: str
    document: str | None
    created_at: datetime
    asaas_customer_id: str | None
    quotes_count: int
    orders_count: int


class AdminClientListRead(BaseModel):
    items: list[AdminClientListItem]
    total: int
    page: int
    page_size: int


class AdminClientCounts(BaseModel):
    quotes: int
    orders: int
    invoices: int
    catalog_entries: int
    conversations: int


class AdminClientDetailRead(BaseModel):
    id: uuid.UUID
    name: str
    business_name: str | None
    email: str
    document: str | None
    phone: str | None
    created_at: datetime
    asaas_customer_id: str | None
    asaas_status: Literal["vinculado", "em_analise", "nao_vinculado"]
    counts: AdminClientCounts


class AdminOrderListItem(BaseModel):
    id: uuid.UUID
    quote_id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    status: str
    amount: Decimal
    created_at: datetime


class AdminOrderListRead(BaseModel):
    items: list[AdminOrderListItem]
    total: int
    page: int
    page_size: int


class AdminInvoiceListItem(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    account_name: str
    asaas_invoice_id: str
    amount: Decimal
    currency: str
    status: str
    due_date: date
    payment_link: str | None


class AdminInvoiceListRead(BaseModel):
    items: list[AdminInvoiceListItem]
    total: int
    page: int
    page_size: int
    synced_at: datetime | None


AdminActivityKind = Literal[
    "invoice_paid", "quote_approved", "client_created", "webhook_processed"
]


class AdminActivityRead(BaseModel):
    kind: AdminActivityKind
    message: str
    at: datetime


class AdminDashboardRead(BaseModel):
    quotes_to_review: int
    active_orders: int
    receivable_open_amount: Decimal
    overdue_amount: Decimal
    overdue_invoice_count: int
    overdue_client_name: str | None
    recent_activity: list[AdminActivityRead]


class AdminCatalogEntryRead(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    type: str
    description: str
    created_at: datetime


class AdminCatalogListRead(BaseModel):
    items: list[AdminCatalogEntryRead]


class AdminConversationListItem(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID | None
    account_name: str | None
    message_count: int
    resulting_quote_id: uuid.UUID | None
    started_at: datetime


class AdminConversationListRead(BaseModel):
    items: list[AdminConversationListItem]
    total: int
    page: int
    page_size: int


class AdminConversationMessageRead(BaseModel):
    role: Literal["assistant", "visitor"]
    content: str


class AdminConversationDraftRead(BaseModel):
    scope_summary: str
    price_range: str | None
    timeline: str | None


class AdminConversationDetailRead(AdminConversationListItem):
    messages: list[AdminConversationMessageRead]
    draft: AdminConversationDraftRead | None


class AdminQuoteListRead(BaseModel):
    items: list[QuoteRead]
    total: int
    page: int
    page_size: int
