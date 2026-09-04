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
    # user turns may also carry a "selected" dict (field + option_ids) when the
    # visitor answered by tapping option chips -- see SelectionMeta.
    transcript: list[dict[str, Any]]
    ready_to_submit: bool
    draft_preview: dict[str, Any] | None
    created_at: datetime


class SelectionMeta(BaseModel):
    """Set when the visitor answered a turn by tapping option chips/checkboxes
    (Fluxo Cotação IA, canvas screen 3) instead of typing. Recorded verbatim on
    the user turn in the transcript for the reviewer -- butter_mind still only
    receives the synthesized natural-language `content`, never this."""

    field: str
    option_ids: list[str]


class ConversationMessageCreate(BaseModel):
    content: str
    selection: SelectionMeta | None = None


class ConversationTurn(BaseModel):
    """`preview` is butter_mind's diagnosis preview, relayed **verbatim**.
    devbutter_platform is a pure passthrough for it -- it never interprets or
    reshapes the preview, only persists it in `Conversation.draft_preview` and
    forwards it to the browser. The canonical shape lives in ONE place:
    butter_mind `app/diagnosis/schemas.py::DiagnosisPreview` (snapshot in
    `contracts/conversation_state.schema.json` there). Re-declaring it here was
    a third hand-maintained copy that drifted -- see ADR-0005."""

    reply: str
    ready_to_submit: bool
    preview: dict[str, Any] | None = None


class SelectOptionRequest(BaseModel):
    """POST /conversations/{id}/select-option -- the visitor picked a path."""

    option_key: str


class BusinessProfilePatch(BaseModel):
    """PATCH /conversations/{id}/business-profile -- partial merge of the
    collecting-step fields (ADR-0004). Relayed verbatim to butter_mind."""

    model_config = ConfigDict(extra="forbid")

    business_name: str | None = None
    segment: str | None = None
    city: str | None = None
    volume: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


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
    # Contact fields the visitor fills/confirms on the 1e form. `contact_name`
    # backs User.name; `contact_company`, when present, overlays company_name --
    # an empty string means "não tenho empresa, é pra mim mesmo" and clears it.
    contact_name: str | None = None
    contact_company: str | None = None
    # Structured overrides from the "pronto para enviar" / contact step (canvas
    # screens 1d-1e): the visitor edits the checkbox group of services and the
    # budget/timeline radios before confirming. Each, when present, replaces
    # whatever butter_mind's extraction captured for that field.
    services_of_interest: list[str] | None = None
    budget_range: str | None = None
    timeline: str | None = None


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
