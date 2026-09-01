from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.conversations.repository import ConversationRepository
from app.conversations.service import ConversationService
from app.core.clients.butter_mind_client import ButterMindClient, get_butter_mind_client
from app.core.dependencies import DbSession


def get_conversation_service(
    db: DbSession,
    mind_client: Annotated[ButterMindClient, Depends(get_butter_mind_client)],
) -> ConversationService:
    return ConversationService(ConversationRepository(db), mind_client)


ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
