from uuid import UUID

from app.accounts.models import User
from app.core.clients.butter_mind_client import get_butter_mind_client
from app.main import app
from tests.conftest import auth_headers, signup


class FakeMindClient:
    async def get_dashboard_overview(self):
        return {
            "diagnosis_sessions": 4,
            "sessions_in_progress": 2,
            "submitted_requests": 1,
            "possibly_ungrounded_requests": 1,
            "assistant_turns": 6,
            "grounded_turns": 4,
            "turns_without_token_usage": 2,
            "input_tokens": 120,
            "cached_input_tokens": 30,
            "output_tokens": 40,
            "knowledge_sources": 3,
            "knowledge_chunks": 12,
            "last_knowledge_update_at": None,
            "model_usage": [],
        }

    async def get_dashboard_settings(self):
        return {
            "diagnosis_max_output_tokens": 1024,
            "diagnosis_max_history_messages": 12,
            "diagnosis_max_turns": 30,
            "diagnosis_grounding_top_k": 3,
            "diagnosis_grounding_min_score": 0.35,
            "uses_runtime_override": False,
            "maritaca_model": "fake-model",
            "embeddings_model_name": "fake-embeddings",
        }

    async def update_dashboard_settings(self, payload):
        return {**await self.get_dashboard_settings(), **payload, "uses_runtime_override": True}


async def test_admin_mind_routes_are_admin_only_and_proxy_private_data(client, db_session):
    user_tokens = await signup(client, email="mind-user@example.com")
    admin_tokens = await signup(client, email="mind-admin@example.com")
    admin_me = await client.get("/accounts/me", headers=auth_headers(admin_tokens))
    admin_user = await db_session.get(User, UUID(admin_me.json()["user_id"]))
    assert admin_user is not None
    admin_user.is_admin = True
    await db_session.commit()

    app.dependency_overrides[get_butter_mind_client] = lambda: FakeMindClient()

    forbidden = await client.get("/admin/mind/overview", headers=auth_headers(user_tokens))
    assert forbidden.status_code == 403

    overview = await client.get("/admin/mind/overview", headers=auth_headers(admin_tokens))
    assert overview.status_code == 200
    assert overview.json()["input_tokens"] == 120

    updated = await client.patch(
        "/admin/mind/settings",
        headers=auth_headers(admin_tokens),
        json={
            "diagnosis_max_output_tokens": 768,
            "diagnosis_max_history_messages": 10,
            "diagnosis_max_turns": 20,
            "diagnosis_grounding_top_k": 4,
            "diagnosis_grounding_min_score": 0.5,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["uses_runtime_override"] is True
    assert updated.json()["diagnosis_max_output_tokens"] == 768
