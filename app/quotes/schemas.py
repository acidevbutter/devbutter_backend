import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class QuoteManualCreate(BaseModel):
    account_id: uuid.UUID
    scope_summary: str
    price_range: str | None = None
    timeline: str | None = None


class QuoteEdit(BaseModel):
    scope_summary: str | None = None
    price_range: str | None = None
    timeline: str | None = None


class QuoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str
    account_id: uuid.UUID
    conversation_id: uuid.UUID | None
    source: str
    status: str
    scope_summary: str
    price_range: str | None
    timeline: str | None
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    created_at: datetime
