import uuid
from typing import Any

from app.conversations.repository import ConversationRepository
from app.core.clients.butter_mind_client import ButterMindClient
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError


@log_errors
class AdminMindService:
    """Admin-only bridge to the private butter_mind service."""

    def __init__(self, conversations: ConversationRepository, mind_client: ButterMindClient):
        self.conversations = conversations
        self.mind_client = mind_client

    async def dashboard_overview(self) -> dict[str, Any]:
        return await self.mind_client.get_dashboard_overview()

    async def governance(self) -> dict[str, Any]:
        return await self.mind_client.get_dashboard_settings()

    async def update_governance(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.mind_client.update_dashboard_settings(payload)

    async def llm_usage_summary(self) -> dict[str, Any]:
        return await self.mind_client.get_llm_usage_summary()

    async def llm_budget(self, flow: str) -> dict[str, Any] | None:
        return await self.mind_client.get_llm_budget(flow)

    async def update_llm_budget(self, flow: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.mind_client.update_llm_budget(flow, payload)

    async def conversation_turn_metrics(self, conversation_id: uuid.UUID) -> list[dict[str, Any]]:
        conversation = await self.conversations.get(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        if not conversation.mind_session_id:
            return []
        return await self.mind_client.get_diagnosis_turn_metrics(conversation.mind_session_id)
