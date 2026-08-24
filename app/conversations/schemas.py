import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.accounts.schemas import TokenPair


class ConversationCreate(BaseModel):
    pass


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID | None
    status: str
    transcript: list[dict[str, str]]
    ready_to_submit: bool
    draft_preview: dict[str, Any] | None
    created_at: datetime


class ConversationMessageCreate(BaseModel):
    content: str


class ConversationPreview(BaseModel):
    problem_summary: str
    services_of_interest: list[str]
    budget_range: str | None = None
    timeline: str | None = None
    missing_information: list[str]
    grounding_status: str


class ConversationTurn(BaseModel):
    reply: str
    ready_to_submit: bool
    preview: ConversationPreview | None = None


class DraftQuoteRequest(BaseModel):
    """Optional overrides submitted from the Fluxo Cotação IA contact step
    (canvas screen 1e). `email`/`phone`, when present, overlay whatever
    butter_mind's extraction captured conversationally before the account is
    resolved -- lets the visitor supply or correct contact info the AI
    didn't catch. `password`, when present, creates the new account with a
    real password (auth_method="password") instead of the passwordless
    magic_link path -- see AccountService.get_or_create_from_extraction.
    """

    email: str | None = None
    phone: str | None = None
    password: str | None = None


class DraftQuoteFromConversation(BaseModel):
    """Returned after `POST /conversations/{id}/draft-quote`. Shows the AI's
    price_range/timeline/reference once, right here -- the Fluxo Cotação IA
    canvas (screens 1d-1g) displays the estimate immediately, clearly
    labeled as unreviewed. This does NOT reopen the general redaction in
    quotes/router.py::_client_facing_quote_read: a quote fetched later via
    GET /quotes/{id} while still `draft` still comes back with price_range
    stripped, per docs/plano-preco-so-apos-aprovacao.md -- only this
    just-submitted response is an intentional, narrow exception.

    `tokens`/`pending_account_link` only matter for a visitor conversation
    (account_id was None going in -- see
    docs/plano-onboarding-conversa-primeiro.md): `tokens` is set when a new
    account was created on the spot, so the frontend can authenticate the
    visitor immediately. `pending_account_link=True` instead means the
    contact email matched an existing account -- no session is issued, the
    quote is saved under that account, and its owner is notified out of
    band; the frontend should tell the visitor to check their email rather
    than treat them as logged in.
    """

    quote_id: uuid.UUID
    reference: str
    scope_summary: str
    price_range: str | None = None
    timeline: str | None = None
    tokens: TokenPair | None = None
    pending_account_link: bool = False
