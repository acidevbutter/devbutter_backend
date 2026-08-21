import uuid
from typing import Any

from app.conversations.models import Conversation
from app.conversations.repository import ConversationRepository
from app.core.clients.butter_mind_client import ButterMindClient
from app.core.decorators import log_errors
from app.core.exceptions import ValidationFailedError


@log_errors
class ConversationService:
    def __init__(self, repository: ConversationRepository, mind_client: ButterMindClient):
        self.repository = repository
        self.mind_client = mind_client

    async def start(self, account_id: uuid.UUID | None) -> Conversation:
        mind_session = await self.mind_client.create_diagnosis_session(str(uuid.uuid4()))
        return await self.repository.create(account_id, mind_session["id"])

    async def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        return await self.repository.get(conversation_id)

    async def send_message(self, conversation: Conversation, content: str) -> tuple[str, bool]:
        turn = await self.mind_client.send_diagnosis_message(conversation.mind_session_id, content)
        reply = turn["message"]["content"]
        await self.repository.append_messages(
            conversation,
            {"role": "user", "content": content},
            {"role": "assistant", "content": reply},
        )
        return reply, turn["ready_to_submit"]

    async def submit_diagnosis(self, conversation: Conversation) -> dict[str, Any]:
        """Finalizes the butter_mind session into a structured, schema-validated
        extraction. Requires an account link -- a Quote must have an owner
        (see architecture doc §4, "Draft enters... if they don't have an
        account yet they're prompted to create one").
        """
        if conversation.account_id is None:
            raise ValidationFailedError(
                "Link an account to this conversation before drafting a quote"
            )
        request = await self.mind_client.submit_diagnosis(conversation.mind_session_id)
        await self.repository.mark_completed(conversation)
        return request
