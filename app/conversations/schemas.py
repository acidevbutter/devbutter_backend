import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConversationCreate(BaseModel):
    pass


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID | None
    status: str
    transcript: list[dict[str, str]]
    created_at: datetime


class ConversationMessageCreate(BaseModel):
    content: str


class ConversationTurn(BaseModel):
    reply: str
    ready_to_submit: bool


class DraftQuoteFromConversation(BaseModel):
    """Returned after `POST /conversations/{id}/draft-quote`. Mirrors the
    validated fields butter_mind's DiagnosisExtraction produces -- see
    core/clients/butter_mind_client.py and quotes/service.create_from_conversation.
    """

    quote_id: uuid.UUID
    scope_summary: str
    price_range: str | None
    timeline: str | None
