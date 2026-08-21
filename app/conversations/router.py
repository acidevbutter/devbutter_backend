import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.accounts.router import CurrentAccount
from app.conversations.models import Conversation
from app.conversations.repository import ConversationRepository
from app.conversations.schemas import (
    ConversationMessageCreate,
    ConversationRead,
    ConversationTurn,
    DraftQuoteFromConversation,
)
from app.conversations.service import ConversationService
from app.core.clients.butter_mind_client import ButterMindClient, get_butter_mind_client
from app.core.dependencies import DbSession
from app.core.exceptions import NotFoundError
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

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _get_owned_conversation(
    conversation_id: uuid.UUID, account: CurrentAccount, service: ConversationServiceDep
) -> Conversation:
    conversation = await service.get(conversation_id)
    if conversation is None or conversation.account_id != account.id:
        raise NotFoundError("Conversation not found")
    return conversation


@router.post(
    "",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a concierge chat session",
)
async def start_conversation(
    account: CurrentAccount, service: ConversationServiceDep
) -> ConversationRead:
    conversation = await service.start(account.id)
    return ConversationRead.model_validate(conversation)


@router.get("/{conversation_id}", response_model=ConversationRead, summary="Get a conversation")
async def get_conversation(
    conversation_id: uuid.UUID, account: CurrentAccount, service: ConversationServiceDep
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
    account: CurrentAccount,
    service: ConversationServiceDep,
) -> ConversationTurn:
    conversation = await _get_owned_conversation(conversation_id, account, service)
    reply, ready_to_submit = await service.send_message(conversation, payload.content)
    return ConversationTurn(reply=reply, ready_to_submit=ready_to_submit)


@router.post(
    "/{conversation_id}/draft-quote",
    response_model=DraftQuoteFromConversation,
    status_code=status.HTTP_201_CREATED,
    summary="Finalize the conversation into a draft Quote (source=ai)",
)
async def draft_quote(
    conversation_id: uuid.UUID,
    account: CurrentAccount,
    conversation_service: ConversationServiceDep,
    quote_service: QuoteServiceDep,
) -> DraftQuoteFromConversation:
    conversation = await _get_owned_conversation(conversation_id, account, conversation_service)
    extraction = await conversation_service.submit_diagnosis(conversation)
    quote = await quote_service.create_from_conversation(
        account_id=account.id, conversation_id=conversation.id, extraction=extraction
    )
    return DraftQuoteFromConversation(
        quote_id=quote.id,
        scope_summary=quote.scope_summary,
        price_range=quote.price_range,
        timeline=quote.timeline,
    )
