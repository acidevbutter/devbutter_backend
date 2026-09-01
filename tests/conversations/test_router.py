import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Conversation
from app.conversations.repository import ConversationRepository
from app.core.clients.asaas_client import get_asaas_client
from app.core.clients.butter_mind_client import get_butter_mind_client
from app.core.clients.relay_client import get_relay_client
from app.core.exceptions import UpstreamServiceError
from app.main import app
from app.quotes.repository import QuoteRepository
from tests.conftest import auth_headers, signup
from tests.test_admin_approval_flow import FakeAsaasClient


class _FakeButterMindClient:
    """Stands in for the real butter_mind service. `extraction` is what
    submit_diagnosis returns -- shaped like butter_mind's DiagnosisExtraction
    (see butter_mind_client.py): contact_name/contact_email/contact_phone/
    company_name/cnpj alongside the existing scope fields.
    """

    def __init__(self, extraction: dict):
        self._extraction = extraction

    async def create_diagnosis_session(self, session_id: str) -> dict:
        return {"id": f"mind-{session_id}"}

    async def send_diagnosis_message(self, mind_session_id: str, content: str) -> dict:
        return {"message": {"content": "Entendido."}, "ready_to_submit": True}

    async def submit_diagnosis(self, mind_session_id: str) -> dict:
        return self._extraction


class _FakeRelayClient:
    def __init__(self):
        self.sent: list[tuple[str, str, dict]] = []

    def send(self, template_key: str, to: str, payload: dict) -> None:
        self.sent.append((template_key, to, payload))


def _extraction(**overrides) -> dict:
    payload = {
        "problem_summary": "Preciso de uma plataforma de agendamento",
        "services_of_interest": ["platform"],
        "budget_range": "R$15.000 - R$25.000",
        "timeline": "2 meses",
        "contact_name": "Visitante Anonimo",
        "contact_email": "visitante-anonimo@example.com",
        "contact_phone": "+5511988887777",
        "company_name": "Startup Visitante",
        "cnpj": "98765432000110",
    }
    payload.update(overrides)
    return payload


def _mock_butter_mind(extraction: dict) -> None:
    app.dependency_overrides[get_butter_mind_client] = lambda: _FakeButterMindClient(extraction)


def _mock_relay() -> _FakeRelayClient:
    fake = _FakeRelayClient()
    app.dependency_overrides[get_relay_client] = lambda: fake
    return fake


async def _run_conversation(client: AsyncClient, headers: dict | None = None) -> str:
    start = await client.post("/conversations", headers=headers or {})
    assert start.status_code == 201, start.text
    conversation_id = start.json()["id"]

    message = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Quero um orcamento."},
        headers=headers or {},
    )
    assert message.status_code == 200, message.text
    return conversation_id


async def test_visitor_new_email_gets_account_created_and_tokens_on_draft_quote(
    client: AsyncClient, db_session: AsyncSession
):
    _mock_butter_mind(_extraction())
    _mock_relay()
    try:
        conversation_id = await _run_conversation(client)

        response = await client.post(f"/conversations/{conversation_id}/draft-quote")

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["tokens"] is not None
        assert body["pending_account_link"] is False
        # Shown once, right here -- see DraftQuoteFromConversation's
        # docstring. GET /quotes/{id} still redacts price on a draft quote
        # (see tests/test_quotes.py); this endpoint is a narrow exception.
        assert body["price_range"] == "R$15.000 - R$25.000"
        assert body["timeline"] == "2 meses"
        assert body["reference"].startswith("COT-")

        me = await client.get("/accounts/me", headers=auth_headers(body["tokens"]))
        assert me.status_code == 200
        assert me.json()["email"] == "visitante-anonimo@example.com"
        assert me.json()["company_name"] == "Startup Visitante"

        quotes = await QuoteRepository(db_session).list_by_account(uuid.UUID(me.json()["id"]))
        assert len(quotes) == 1
        assert quotes[0].source == "ai"
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)


async def test_visitor_existing_email_does_not_auto_login_and_notifies_owner(
    client: AsyncClient, db_session: AsyncSession
):
    owner_tokens = await signup(
        client, email="dono-existente@example.com", name="Dono Existente"
    )
    owner_account = await client.get("/accounts/me", headers=auth_headers(owner_tokens))
    owner_account_id = owner_account.json()["id"]

    _mock_butter_mind(_extraction(contact_email="dono-existente@example.com"))
    fake_relay = _mock_relay()
    try:
        conversation_id = await _run_conversation(client)

        response = await client.post(f"/conversations/{conversation_id}/draft-quote")

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["tokens"] is None
        assert body["pending_account_link"] is True

        assert len(fake_relay.sent) == 1
        template_key, to, _payload = fake_relay.sent[0]
        assert template_key == "devbutter_pending_account_link"
        assert to == "dono-existente@example.com"

        quotes = await QuoteRepository(db_session).list_by_account(uuid.UUID(owner_account_id))
        assert len(quotes) == 1
        assert quotes[0].source == "ai"

        # The visitor never received a session for the existing account.
        me_without_token = await client.get("/accounts/me")
        assert me_without_token.status_code == 401
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)


async def test_logged_in_flow_still_links_conversation_to_own_account(client: AsyncClient):
    tokens = await signup(client, email="logged-in-client@example.com")
    headers = auth_headers(tokens)
    account_id = (await client.get("/accounts/me", headers=headers)).json()["id"]

    _mock_butter_mind(_extraction(contact_email="logged-in-client@example.com"))
    try:
        conversation_id = await _run_conversation(client, headers=headers)

        started = await client.get(f"/conversations/{conversation_id}", headers=headers)
        assert started.json()["account_id"] == account_id

        response = await client.post(
            f"/conversations/{conversation_id}/draft-quote", headers=headers
        )

        assert response.status_code == 201, response.text
        body = response.json()
        # Already logged in -- no new account/session is issued.
        assert body["tokens"] is None
        assert body["pending_account_link"] is False
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)


async def test_resumed_conversation_returns_transcript_and_ready_to_submit(client: AsyncClient):
    _mock_butter_mind(_extraction())
    try:
        conversation_id = await _run_conversation(client)

        resumed = await client.get(f"/conversations/{conversation_id}")

        assert resumed.status_code == 200, resumed.text
        body = resumed.json()
        assert body["ready_to_submit"] is True
        assert len(body["transcript"]) == 2
        assert body["transcript"][0] == {"role": "user", "content": "Quero um orcamento."}
        assert body["transcript"][1] == {"role": "assistant", "content": "Entendido."}
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)


async def test_draft_quote_email_and_phone_overrides_the_extraction(
    client: AsyncClient, db_session: AsyncSession
):
    _mock_butter_mind(_extraction())
    _mock_relay()
    try:
        conversation_id = await _run_conversation(client)

        response = await client.post(
            f"/conversations/{conversation_id}/draft-quote",
            json={"email": "corrigido@example.com", "phone": "+5511900000000"},
        )

        assert response.status_code == 201, response.text
        tokens = response.json()["tokens"]
        me = await client.get("/accounts/me", headers=auth_headers(tokens))
        assert me.json()["email"] == "corrigido@example.com"
        assert me.json()["phone"] == "+5511900000000"
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)


async def test_draft_quote_with_inline_password_skips_setup_token_email(client: AsyncClient):
    _mock_butter_mind(_extraction(contact_email="senha-inline@example.com"))
    fake_relay = _mock_relay()
    try:
        conversation_id = await _run_conversation(client)

        response = await client.post(
            f"/conversations/{conversation_id}/draft-quote",
            json={"password": "uma-senha-forte-123"},
        )

        assert response.status_code == 201, response.text
        assert len(fake_relay.sent) == 1
        template_key, to, payload = fake_relay.sent[0]
        assert template_key == "devbutter_quote_received"
        assert to == "senha-inline@example.com"
        assert payload["password_setup_url"] is None

        # The password set inline logs the visitor in directly too.
        login = await client.post(
            "/accounts/login",
            json={"email": "senha-inline@example.com", "password": "uma-senha-forte-123"},
        )
        assert login.status_code == 200, login.text
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)


async def test_draft_quote_without_password_issues_a_one_time_setup_link(client: AsyncClient):
    _mock_butter_mind(_extraction(contact_email="sem-senha@example.com"))
    fake_relay = _mock_relay()
    try:
        conversation_id = await _run_conversation(client)

        response = await client.post(f"/conversations/{conversation_id}/draft-quote")

        assert response.status_code == 201, response.text
        template_key, to, payload = fake_relay.sent[0]
        assert template_key == "devbutter_quote_received"
        assert to == "sem-senha@example.com"
        assert payload["password_setup_url"] is not None
        token = payload["password_setup_url"].rsplit("/", 1)[-1]

        check = await client.get(f"/accounts/password-setup/{token}")
        assert check.status_code == 200
        assert check.json()["email"] == "sem-senha@example.com"

        complete = await client.post(
            "/accounts/password-setup", json={"token": token, "password": "outra-senha-123"}
        )
        assert complete.status_code == 200, complete.text
        assert complete.json()["access_token"]

        # One-time use.
        reuse = await client.post(
            "/accounts/password-setup", json={"token": token, "password": "outra-senha-456"}
        )
        assert reuse.status_code == 404
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)


async def test_quote_references_are_unique_and_sequential(
    client: AsyncClient, db_session: AsyncSession
):
    _mock_relay()
    _mock_butter_mind(_extraction(contact_email="ref-um@example.com"))
    try:
        first_id = await _run_conversation(client)
        first = await client.post(f"/conversations/{first_id}/draft-quote")
        assert first.status_code == 201, first.text
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)

    _mock_butter_mind(_extraction(contact_email="ref-dois@example.com"))
    try:
        second_id = await _run_conversation(client)
        second = await client.post(f"/conversations/{second_id}/draft-quote")
        assert second.status_code == 201, second.text
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)

    first_ref = first.json()["reference"]
    second_ref = second.json()["reference"]
    assert first_ref != second_ref
    assert int(second_ref.split("-")[1]) == int(first_ref.split("-")[1]) + 1


class _SessionAwareButterMindClient(_FakeButterMindClient):
    """Tracks which mind_session_ids it has handed out; a message to any other
    id fails the way butter_mind does when its own session store was reset
    (404), so ConversationService's self-heal can be exercised.
    """

    def __init__(self, extraction: dict):
        super().__init__(extraction)
        self.known: set[str] = set()
        self.created_sessions = 0

    async def create_diagnosis_session(self, session_id: str) -> dict:
        self.created_sessions += 1
        mind_id = f"mind-{session_id}"
        self.known.add(mind_id)
        return {"id": mind_id}

    async def send_diagnosis_message(self, mind_session_id: str, content: str) -> dict:
        if mind_session_id not in self.known:
            raise UpstreamServiceError(
                f"butter_mind returned 404 for POST /diagnosis/sessions/{mind_session_id}/messages",
                status_code=404,
            )
        return await super().send_diagnosis_message(mind_session_id, content)


async def test_message_self_heals_when_mind_session_is_gone(
    client: AsyncClient, db_session: AsyncSession
):
    fake = _SessionAwareButterMindClient(_extraction())
    app.dependency_overrides[get_butter_mind_client] = lambda: fake
    try:
        start = await client.post("/conversations")
        assert start.status_code == 201, start.text
        conversation_id = uuid.UUID(start.json()["id"])
        assert fake.created_sessions == 1

        # butter_mind lost this session (its store was reset independently).
        repository = ConversationRepository(db_session)
        conversation = await repository.get(conversation_id)
        stale_id = conversation.mind_session_id
        await repository.set_mind_session_id(conversation, f"mind-{uuid.uuid4()}")

        message = await client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "Quero um orcamento."},
        )

        assert message.status_code == 200, message.text
        assert message.json()["reply"] == "Entendido."
        assert fake.created_sessions == 2

        db_session.expire_all()
        healed = await db_session.get(Conversation, conversation_id)
        assert healed.mind_session_id != stale_id
        assert healed.mind_session_id in fake.known
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)


async def test_visitor_draft_quote_flow_never_calls_asaas(client: AsyncClient):
    _mock_butter_mind(_extraction(contact_email="sem-asaas@example.com"))
    _mock_relay()
    fake_asaas = FakeAsaasClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    try:
        conversation_id = await _run_conversation(client)
        response = await client.post(f"/conversations/{conversation_id}/draft-quote")
        assert response.status_code == 201, response.text
        assert fake_asaas.created_customers == []
        assert fake_asaas.created_payments == []
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)
        app.dependency_overrides.pop(get_asaas_client, None)
