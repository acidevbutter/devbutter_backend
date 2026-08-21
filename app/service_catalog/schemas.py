import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ServiceCatalogEntryCreate(BaseModel):
    type: str
    description: str


class ServiceCatalogEntryUpdate(BaseModel):
    type: str | None = None
    description: str | None = None


class ServiceCatalogEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    type: str
    description: str
    created_at: datetime
