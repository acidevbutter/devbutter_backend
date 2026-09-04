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


class NextStepOption(BaseModel):
    id: str
    label: str


class NextStep(BaseModel):
    """Passthrough of butter_mind's structured "pick one/some" block for the
    current qualification turn -- devbutter_platform does not interpret the
    option ids, it only relays this to the browser and persists it in
    Conversation.draft_preview so a resumed chat can re-render the options."""

    prompt: str
    field: str
    selection_mode: str
    allow_free_text: bool = True
    options: list[NextStepOption]


class BusinessMetric(BaseModel):
    """Passthrough of one butter_mind "números do negócio" stat (Fluxo Cotação
    IA, canvas screen 2a). `value` is free text; `computed` marks a figure the
    AI derived rather than one the visitor stated."""

    label: str
    value: str
    computed: bool = False


class BusinessProfile(BaseModel):
    """Passthrough of butter_mind's live business profile for the chat's chip
    row (canvas screen 1c) and the portal's live dossiê (canvas screen 2a).
    devbutter_platform does not interpret it -- it relays it to the browser and
    persists it verbatim in Conversation.draft_preview so a resumed chat
    re-renders the same chips/dossiê."""

    company_name: str | None = None
    is_legal_entity: bool | None = None
    segment: str | None = None
    city: str | None = None
    volume: str | None = None
    metrics: list[BusinessMetric] = []
    pain_points: list[str] = []
    scope_modules: list[str] = []
    contact_ready: bool = False


class QualificationProgress(BaseModel):
    answered: int
    total: int


class ProductOption(BaseModel):
    """Passthrough of one butter_mind proposed path (ADR-0004). devbutter does
    not interpret the key -- it relays the card and the chosen key back."""

    key: str
    title: str
    summary: str
    price_range: str | None = None
    timeline: str | None = None
    recommended: bool = False


class ConversationPreview(BaseModel):
    problem_summary: str
    services_of_interest: list[str]
    budget_range: str | None = None
    timeline: str | None = None
    missing_information: list[str]
    grounding_status: str
    next_step: NextStep | None = None
    # Live qualification state (canvas screen 1c). Optional so a preview
    # produced by an older butter_mind still validates.
    business_profile: BusinessProfile | None = None
    progress: QualificationProgress | None = None
    # ADR-0004 turno-2: stage machine + proposed/chosen paths.
    stage: str = "qualifying"
    options: list[ProductOption] = []
    selected_option_key: str | None = None
    missing_fields: list[str] = []
    # Passthrough of butter_mind's _OPTION_CATALOG for the fields the review
    # step edits (canvas 1d-1e): "services_of_interest", "budget_range",
    # "timeline". The browser renders the review checkboxes/pills from this
    # instead of a hardcoded copy. Empty from an older butter_mind.
    field_options: dict[str, list[NextStepOption]] = {}


class ConversationTurn(BaseModel):
    reply: str
    ready_to_submit: bool
    preview: ConversationPreview | None = None


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
