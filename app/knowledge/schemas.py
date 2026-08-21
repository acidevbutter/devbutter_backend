import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class KnowledgeEntryCreate(BaseModel):
    name: str
    description: str
    price_min: Decimal
    price_max: Decimal
    typical_duration: str
    ai_instructions: str


class KnowledgeEntryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    typical_duration: str | None = None
    ai_instructions: str | None = None


class KnowledgeEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    price_min: Decimal
    price_max: Decimal
    typical_duration: str
    ai_instructions: str
    updated_at: datetime
