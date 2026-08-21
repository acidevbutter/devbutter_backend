import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Conversation


class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        return await self.session.get(Conversation, conversation_id)

    async def create(self, account_id: uuid.UUID | None, mind_session_id: str) -> Conversation:
        conversation = Conversation(account_id=account_id, mind_session_id=mind_session_id)
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def append_messages(
        self, conversation: Conversation, *messages: dict[str, str]
    ) -> Conversation:
        conversation.transcript = [*conversation.transcript, *messages]
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def mark_completed(self, conversation: Conversation) -> Conversation:
        conversation.status = "completed"
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation
