import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.conversations.models import Conversation
from app.conversations.repository import ConversationRepository
from app.core.clients.butter_mind_client import ButterMindClient
from app.core.decorators import log_errors


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

    async def send_message(
        self, conversation: Conversation, content: str
    ) -> tuple[str, bool, dict[str, Any] | None]:
        turn = await self.mind_client.send_diagnosis_message(conversation.mind_session_id, content)
        reply = turn["message"]["content"]
        ready_to_submit = turn["ready_to_submit"]
        preview = turn.get("preview")
        await self.repository.append_messages(
            conversation,
            {"role": "user", "content": content},
            {"role": "assistant", "content": reply},
            ready_to_submit=ready_to_submit,
            draft_preview=preview,
        )
        return reply, ready_to_submit, preview

    async def stream_message(
        self, conversation: Conversation, content: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Same persistence as send_message, but relays butter_mind's streamed
        reply chunk by chunk -- used by the Socket.IO `send_message` handler.
        Re-yields each upstream event; on `done` it appends the user + full
        assistant messages to the local transcript mirror, same as send_message.
        """
        full_reply = ""
        async for event in self.mind_client.stream_diagnosis_message(
            conversation.mind_session_id, content
        ):
            if event["type"] == "delta":
                full_reply += event["content"]
            elif event["type"] == "done":
                await self.repository.append_messages(
                    conversation,
                    {"role": "user", "content": content},
                    {"role": "assistant", "content": full_reply},
                    ready_to_submit=event["ready_to_submit"],
                    draft_preview=event.get("preview"),
                )
            yield event

    async def submit_diagnosis(self, conversation: Conversation) -> dict[str, Any]:
        """Finalizes the butter_mind session into a structured, schema-validated
        extraction. The conversation may still be unclaimed (account_id=None)
        at this point -- see docs/plano-onboarding-conversa-primeiro.md: the
        account is resolved *after* this, in
        conversations/router.py::draft_quote, via
        AccountService.get_or_create_from_extraction using the contact fields
        this extraction includes.
        """
        request = await self.mind_client.submit_diagnosis(conversation.mind_session_id)
        await self.repository.mark_completed(conversation)
        return request

    async def link_account(self, conversation: Conversation, account_id: uuid.UUID) -> Conversation:
        return await self.repository.link_account(conversation, account_id)
