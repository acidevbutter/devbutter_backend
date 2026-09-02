import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.quotes.schemas import QuoteRead


class AdminClientListItem(BaseModel):
    id: uuid.UUID
    name: str
    business_name: str | None
    email: str
    cpf: str | None
    cnpj: str | None
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
    cpf: str | None
    cnpj: str | None
    phone: str | None
    created_at: datetime
    asaas_customer_id: str | None
    asaas_status: Literal["vinculado", "em_analise", "nao_vinculado"]
    # Layer B merchant subaccount (asaas_subaccount_links) -- raw Asaas
    # onboarding/approval strings, null when the company has no subaccount.
    subaccount_onboarding: str | None = None
    subaccount_approval: str | None = None
    counts: AdminClientCounts


class AdminOrderListItem(BaseModel):
    id: uuid.UUID
    quote_id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    status: str
    amount: Decimal
    created_at: datetime
    invoices_total: int = 0
    invoices_paid: int = 0
    invoices_overdue: int = 0


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


class AdminMindModelUsageRead(BaseModel):
    model: str | None
    turns: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    turns_without_token_usage: int


class AdminMindDashboardRead(BaseModel):
    diagnosis_sessions: int
    sessions_in_progress: int
    submitted_requests: int
    possibly_ungrounded_requests: int
    assistant_turns: int
    grounded_turns: int
    turns_without_token_usage: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    knowledge_sources: int
    knowledge_chunks: int
    last_knowledge_update_at: datetime | None
    model_usage: list[AdminMindModelUsageRead]


class AdminMindGovernanceRead(BaseModel):
    diagnosis_max_output_tokens: int
    diagnosis_max_history_messages: int
    diagnosis_max_turns: int
    diagnosis_grounding_top_k: int
    diagnosis_grounding_min_score: float
    uses_runtime_override: bool
    maritaca_model: str
    embeddings_model_name: str


class AdminMindGovernanceUpdate(BaseModel):
    diagnosis_max_output_tokens: int = Field(ge=128, le=4096)
    diagnosis_max_history_messages: int = Field(ge=2, le=50)
    diagnosis_max_turns: int = Field(ge=1, le=100)
    diagnosis_grounding_top_k: int = Field(ge=1, le=10)
    diagnosis_grounding_min_score: float = Field(ge=0, le=1)


class AdminMindTurnMetricRead(BaseModel):
    id: uuid.UUID
    assistant_message_id: uuid.UUID
    chunks_retrieved: list[dict[str, str | float]]
    chunks_used_count: int
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    model: str | None
    created_at: datetime


class AdminLLMBudgetRead(BaseModel):
    flow: str
    monthly_budget_brl: Decimal
    updated_at: datetime


class AdminLLMBudgetUpdate(BaseModel):
    monthly_budget_brl: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


class AdminLLMFlowUsageRead(BaseModel):
    flow: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    estimated_cost_brl: Decimal
    monthly_budget_brl: Decimal | None


class AdminLLMUsageSummaryRead(BaseModel):
    month: str
    flows: list[AdminLLMFlowUsageRead]


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


class AdminCompanyListItem(BaseModel):
    id: uuid.UUID
    name: str
    cnpj: str | None
    owner_name: str
    has_payment_method: bool
    onboarding_status: str | None
    approval_status: str | None
    split_quota_percent: Decimal | None
    used_percent: Decimal | None


class AdminCompanyListRead(BaseModel):
    items: list[AdminCompanyListItem]
    total: int
    page: int
    page_size: int


class AdminSubaccountLinkRead(BaseModel):
    asaas_account_id: str
    wallet_id: str | None
    onboarding_status: str | None
    approval_status: str | None
    split_quota_percent: Decimal | None
    quota_set_by: uuid.UUID | None
    quota_updated_at: datetime | None


class AdminCompanyDetailRead(BaseModel):
    id: uuid.UUID
    name: str
    cnpj: str | None
    owner_name: str
    owner_email: str
    link: AdminSubaccountLinkRead | None
    split_rules: list[dict[str, Any]]
    access_tokens: dict[str, Any]
    used_percent: Decimal | None


class AdminSplitQuotaUpdate(BaseModel):
    split_quota_percent: Decimal | None


class AdminLinkSubaccountRequest(BaseModel):
    """References an existing Asaas subaccount by id. The subaccount must
    already exist on Asaas (provisioned through the asaas_payment proxy);
    this only records the read-only company <-> subaccount link -- see
    AdminCompaniesService.link_subaccount.
    """

    model_config = ConfigDict(extra="allow")

    asaas_account_id: str | None = None


class AdminCompanyFeatureRead(BaseModel):
    id: uuid.UUID
    feature_code: str
    status: str
    asaas_access_token_id: str | None
    enabled_by: uuid.UUID | None
    enabled_at: datetime | None
    disabled_at: datetime | None
    last_error: str | None
