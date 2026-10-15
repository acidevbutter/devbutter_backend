import uuid

from fastapi import APIRouter, status

from app.accounts.dependencies import AccountServiceDep, OptionalCurrentAccount
from app.conversations.dependencies import ConversationServiceDep
from app.conversations.models import Conversation
from app.conversations.redaction import redact_preview
from app.conversations.schemas import (
    BusinessProfilePatch,
    ConversationMessageCreate,
    ConversationRead,
    ConversationTurn,
    DraftQuoteFromConversation,
    DraftQuoteRequest,
    SelectOptionRequest,
)
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.notifications.dependencies import NotificationServiceDep
from app.quotes.dependencies import QuoteServiceDep

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _get_owned_conversation(
    conversation_id: uuid.UUID, account: OptionalCurrentAccount, service: ConversationServiceDep
) -> Conversation:
    conversation = await service.get(conversation_id)
    if conversation is None:
        raise NotFoundError("Conversation not found")
    # An unclaimed (account_id=None) conversation is a visitor chat that
    # hasn't produced a Quote yet -- anyone holding its id may keep using it
    # (see docs/plano-onboarding-conversa-primeiro.md). Once account_id is
    # set, only that account may access it.
    if conversation.account_id is not None and (
        account is None or conversation.account_id != account.id
    ):
        raise NotFoundError("Conversation not found")
    return conversation


def _client_read(conversation: Conversation) -> ConversationRead:
    """ConversationRead for the visitor/client: the stored draft_preview keeps the
    AI's prices for the admin screens, but they never leave through here (DVB-454)."""
    read = ConversationRead.model_validate(conversation)
    read.draft_preview = redact_preview(read.draft_preview)
    return read


@router.post(
    "",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a concierge chat session",
)
async def start_conversation(
    account: OptionalCurrentAccount, service: ConversationServiceDep
) -> ConversationRead:
    conversation = await service.start(account.id if account else None)
    return _client_read(conversation)


@router.get("/{conversation_id}", response_model=ConversationRead, summary="Get a conversation")
async def get_conversation(
    conversation_id: uuid.UUID, account: OptionalCurrentAccount, service: ConversationServiceDep
) -> ConversationRead:
    conversation = await _get_owned_conversation(conversation_id, account, service)
    return _client_read(conversation)


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationTurn,
    summary="Send a message and get the concierge's reply",
)
async def send_message(
    conversation_id: uuid.UUID,
    payload: ConversationMessageCreate,
    account: OptionalCurrentAccount,
    service: ConversationServiceDep,
) -> ConversationTurn:
    conversation = await _get_owned_conversation(conversation_id, account, service)
    selection = payload.selection.model_dump() if payload.selection else None
    reply, ready_to_submit, preview = await service.send_message(
        conversation, payload.content, selection
    )
    return ConversationTurn(
        reply=reply, ready_to_submit=ready_to_submit, preview=redact_preview(preview)
    )


@router.post(
    "/{conversation_id}/select-option",
    response_model=ConversationTurn,
    status_code=status.HTTP_201_CREATED,
    summary="Pick one of the proposed paths (ADR-0004)",
)
async def select_option(
    conversation_id: uuid.UUID,
    payload: SelectOptionRequest,
    account: OptionalCurrentAccount,
    service: ConversationServiceDep,
) -> ConversationTurn:
    conversation = await _get_owned_conversation(conversation_id, account, service)
    reply, ready_to_submit, preview = await service.select_option(
        conversation, payload.option_key
    )
    return ConversationTurn(
        reply=reply, ready_to_submit=ready_to_submit, preview=redact_preview(preview)
    )


@router.patch(
    "/{conversation_id}/business-profile",
    response_model=ConversationTurn,
    summary="Merge collected business-profile fields (ADR-0004)",
)
async def patch_business_profile(
    conversation_id: uuid.UUID,
    payload: BusinessProfilePatch,
    account: OptionalCurrentAccount,
    service: ConversationServiceDep,
) -> ConversationTurn:
    conversation = await _get_owned_conversation(conversation_id, account, service)
    reply, ready_to_submit, preview = await service.patch_business_profile(
        conversation, payload.model_dump(exclude_unset=True)
    )
    return ConversationTurn(
        reply=reply, ready_to_submit=ready_to_submit, preview=redact_preview(preview)
    )


@router.post(
    "/{conversation_id}/draft-quote",
    response_model=DraftQuoteFromConversation,
    status_code=status.HTTP_201_CREATED,
    summary="Finalize the conversation into a draft Quote (source=ai)",
)
async def draft_quote(
    conversation_id: uuid.UUID,
    account: OptionalCurrentAccount,
    conversation_service: ConversationServiceDep,
    quote_service: QuoteServiceDep,
    account_service: AccountServiceDep,
    notification_service: NotificationServiceDep,
    payload: DraftQuoteRequest | None = None,
) -> DraftQuoteFromConversation:
    payload = payload or DraftQuoteRequest()
    conversation = await _get_owned_conversation(conversation_id, account, conversation_service)
    if not conversation.ready_to_submit:
        raise ValidationFailedError(
            "A conversa ainda não reuniu informações suficientes para enviar o orçamento"
        )
    extraction = await conversation_service.submit_diagnosis(conversation)
    if payload.email:
        extraction["contact_email"] = payload.email
    if payload.phone:
        extraction["contact_phone"] = payload.phone
    if payload.contact_name:
        extraction["contact_name"] = payload.contact_name
    # An explicit "" (the "não tenho empresa" toggle on canvas 1e) clears the
    # conversationally-detected company; a real value overrides it.
    if payload.contact_company is not None:
        extraction["company_name"] = payload.contact_company or None
    # Structured edits the visitor made on the checkbox/radio review step
    # (canvas 1d-1e) win over the conversational extraction for these fields.
    if payload.services_of_interest is not None:
        extraction["services_of_interest"] = payload.services_of_interest
    if payload.budget_range is not None:
        extraction["budget_range"] = payload.budget_range
    if payload.timeline is not None:
        extraction["timeline"] = payload.timeline

    account_id = conversation.account_id
    tokens = None
    pending_account_link = False
    new_account_user = None
    if account_id is None:
        # Visitor conversation reaching draft-quote for the first time --
        # resolve (or create) the owning account from the extraction's own
        # contact fields, per docs/plano-onboarding-conversa-primeiro.md.
        # `payload.password`, when present, comes from the Fluxo Cotação IA
        # contact step (canvas screen 1e) and is ignored for an existing
        # account match (see AccountService.get_or_create_from_extraction).
        link = await account_service.get_or_create_from_extraction(
            extraction, password=payload.password
        )
        account_id = link.account.id
        await conversation_service.link_account(conversation, account_id)
        if link.tokens is not None:
            tokens = link.tokens
            if link.created:
                new_account_user = link.user
        else:
            pending_account_link = True
            notification_service.notify_pending_account_link(link.user, extraction)

    quote = await quote_service.create_from_conversation(
        account_id=account_id, conversation_id=conversation.id, extraction=extraction
    )

    if new_account_user is not None:
        # Brand-new account created on the spot -- confirm the quote by
        # email. No password was typed inline -> also hand them a way back
        # in later (canvas 1e: "ou receba um link mágico por e-mail").
        password_setup_token = (
            None
            if payload.password
            else await account_service.issue_password_setup_token(new_account_user.id)
        )
        notification_service.notify_quote_received(new_account_user, quote, password_setup_token)

    return DraftQuoteFromConversation(
        quote_id=quote.id,
        reference=quote.reference,
        scope_summary=quote.scope_summary,
        timeline=quote.timeline,
        tokens=tokens,
        pending_account_link=pending_account_link,
    )
