import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.accounts.router import AccountServiceDep, OptionalCurrentAccount
from app.conversations.models import Conversation
from app.conversations.repository import ConversationRepository
from app.conversations.schemas import (
    ConversationMessageCreate,
    ConversationRead,
    ConversationTurn,
    DraftQuoteFromConversation,
    DraftQuoteRequest,
)
from app.conversations.service import ConversationService
from app.core.clients.butter_mind_client import ButterMindClient, get_butter_mind_client
from app.core.clients.relay_client import RelayClient, get_relay_client
from app.core.dependencies import DbSession
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.notifications.service import NotificationService
from app.quotes.repository import QuoteRepository
from app.quotes.service import QuoteService


def get_conversation_service(
    db: DbSession, mind_client: Annotated[ButterMindClient, Depends(get_butter_mind_client)]
) -> ConversationService:
    return ConversationService(ConversationRepository(db), mind_client)


ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]


def get_quote_service(db: DbSession) -> QuoteService:
    return QuoteService(QuoteRepository(db))


QuoteServiceDep = Annotated[QuoteService, Depends(get_quote_service)]


def get_notification_service(
    relay_client: Annotated[RelayClient, Depends(get_relay_client)],
) -> NotificationService:
    return NotificationService(relay_client)


NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]

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
    return ConversationRead.model_validate(conversation)


@router.get("/{conversation_id}", response_model=ConversationRead, summary="Get a conversation")
async def get_conversation(
    conversation_id: uuid.UUID, account: OptionalCurrentAccount, service: ConversationServiceDep
) -> ConversationRead:
    conversation = await _get_owned_conversation(conversation_id, account, service)
    return ConversationRead.model_validate(conversation)


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
    reply, ready_to_submit, preview = await service.send_message(conversation, payload.content)
    return ConversationTurn(reply=reply, ready_to_submit=ready_to_submit, preview=preview)


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
    payload: DraftQuoteRequest = DraftQuoteRequest(),
) -> DraftQuoteFromConversation:
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
        price_range=quote.price_range,
        timeline=quote.timeline,
        tokens=tokens,
        pending_account_link=pending_account_link,
    )
