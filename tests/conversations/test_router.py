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

    def __init__(self, extraction: dict, turn_preview: dict | None = None):
        self._extraction = extraction
        self._turn_preview = turn_preview

    async def create_diagnosis_session(self, session_id: str) -> dict:
        return {"id": f"mind-{session_id}"}

    async def send_diagnosis_message(self, mind_session_id: str, content: str) -> dict:
        turn: dict = {"message": {"content": "Entendido."}, "ready_to_submit": True}
        if self._turn_preview is not None:
            turn["preview"] = self._turn_preview
        return turn

    async def select_diagnosis_option(self, mind_session_id: str, option_key: str) -> dict:
        preview = dict(self._turn_preview or {})
        preview["selected_option_key"] = option_key
        preview["stage"] = "ready"
        return {
            "message": {"content": f"Boa escolha — {option_key}."},
            "ready_to_submit": True,
            "preview": preview,
        }

    async def patch_diagnosis_business_profile(
        self, mind_session_id: str, patch: dict
    ) -> dict:
        preview = dict(self._turn_preview or {})
        preview["stage"] = "ready"
        preview["missing_fields"] = []
        return {
            "message": {"content": "Anotado."},
            "ready_to_submit": True,
            "preview": preview,
        }

    async def submit_diagnosis(self, mind_session_id: str, body: dict | None = None) -> dict:
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


def _mock_butter_mind(extraction: dict, turn_preview: dict | None = None) -> None:
    app.dependency_overrides[get_butter_mind_client] = (
        lambda: _FakeButterMindClient(extraction, turn_preview)
    )


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


_NEXT_STEP = {
    "prompt": "Que tipo de solução você imagina?",
    "field": "services_of_interest",
    "selection_mode": "multi",
    "allow_free_text": True,
    "options": [
        {"id": "svc_ai_agent", "label": "Agente de IA"},
        {"id": "svc_automation", "label": "Automação de processos"},
    ],
}


def _preview(**overrides) -> dict:
    payload = {
        "problem_summary": "Automatizar atendimento",
        "services_of_interest": [],
        "budget_range": None,
        "timeline": None,
        "missing_information": ["e-mail"],
        "grounding_status": "unavailable",
        "next_step": _NEXT_STEP,
    }
    payload.update(overrides)
    return payload


async def test_send_message_relays_next_step_and_persists_it_for_resume(client: AsyncClient):
    _mock_butter_mind(_extraction(), turn_preview=_preview())
    try:
        start = await client.post("/conversations")
        conversation_id = start.json()["id"]

        turn = await client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "tenho uma loja e quero automatizar o whatsapp"},
        )
        assert turn.status_code == 200, turn.text
        step = turn.json()["preview"]["next_step"]
        assert step["selection_mode"] == "multi"
        assert [o["id"] for o in step["options"]] == ["svc_ai_agent", "svc_automation"]

        # draft_preview persists it, so a resumed chat re-renders the options.
        resumed = await client.get(f"/conversations/{conversation_id}")
        assert resumed.json()["draft_preview"]["next_step"]["field"] == "services_of_interest"
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)


async def test_send_message_relays_business_profile_city_and_metrics(client: AsyncClient):
    profile = {
        "company_name": "Loja Flor",
        "is_legal_entity": True,
        "segment": "moda / varejo",
        "city": "Curitiba PR",
        "volume": "~150 conversas/dia",
        "metrics": [
            {"label": "conversas / dia", "value": "~150", "computed": False},
            {"label": "/ dia em repetição", "value": "~4h", "computed": True},
        ],
        "pain_points": ["respostas repetitivas de preço e frete"],
        "scope_modules": ["Agente treinado no catálogo"],
        "contact_ready": False,
    }
    _mock_butter_mind(
        _extraction(),
        turn_preview=_preview(business_profile=profile, progress={"answered": 4, "total": 5}),
    )
    try:
        start = await client.post("/conversations")
        conversation_id = start.json()["id"]

        turn = await client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "loja em curitiba, ~150 conversas/dia, 2 atendentes"},
        )
        assert turn.status_code == 200, turn.text
        bp = turn.json()["preview"]["business_profile"]
        assert bp["city"] == "Curitiba PR"
        assert [m["value"] for m in bp["metrics"]] == ["~150", "~4h"]
        assert bp["metrics"][1]["computed"] is True

        # draft_preview keeps the raw dict, so a resumed dossiê re-renders it.
        resumed = await client.get(f"/conversations/{conversation_id}")
        resumed_bp = resumed.json()["draft_preview"]["business_profile"]
        assert resumed_bp["city"] == "Curitiba PR"
        assert resumed_bp["metrics"][1]["value"] == "~4h"
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)


async def test_send_message_records_option_selection_on_the_user_turn(client: AsyncClient):
    _mock_butter_mind(_extraction(), turn_preview=_preview())
    try:
        start = await client.post("/conversations")
        conversation_id = start.json()["id"]

        turn = await client.post(
            f"/conversations/{conversation_id}/messages",
            json={
                "content": "Que tipo de solução: Agente de IA, Automação de processos",
                "selection": {
                    "field": "services_of_interest",
                    "option_ids": ["svc_ai_agent", "svc_automation"],
                },
            },
        )
        assert turn.status_code == 200, turn.text

        resumed = await client.get(f"/conversations/{conversation_id}")
        user_turn = resumed.json()["transcript"][0]
        assert user_turn["role"] == "user"
        assert user_turn["selected"] == {
            "field": "services_of_interest",
            "option_ids": ["svc_ai_agent", "svc_automation"],
        }
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)


async def test_draft_quote_applies_structured_service_budget_timeline_overrides(
    client: AsyncClient, db_session: AsyncSession
):
    _mock_butter_mind(
        _extraction(services_of_interest=["algo vago"], budget_range="?", timeline="?")
    )
    _mock_relay()
    try:
        conversation_id = await _run_conversation(client)

        response = await client.post(
            f"/conversations/{conversation_id}/draft-quote",
            json={
                "services_of_interest": ["Agente de IA", "Automação de processos"],
                "budget_range": "R$ 30–80 mil",
                "timeline": "1–3 meses",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["price_range"] == "R$ 30–80 mil"
        assert body["timeline"] == "1–3 meses"

        me = await client.get("/accounts/me", headers=auth_headers(body["tokens"]))
        quotes = await QuoteRepository(db_session).list_by_account(uuid.UUID(me.json()["id"]))
        assert "Agente de IA, Automação de processos" in quotes[0].scope_summary
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)


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


async def test_draft_quote_contact_name_and_company_override_the_extraction(
    client: AsyncClient,
):
    """Canvas 1e: the visitor types their name and (for a detected pessoa
    jurídica) confirms/edits the company on the contact form."""
    _mock_butter_mind(_extraction(contact_email="novo-contato@example.com"))
    _mock_relay()
    try:
        conversation_id = await _run_conversation(client)

        response = await client.post(
            f"/conversations/{conversation_id}/draft-quote",
            json={"contact_name": "Ana Ribeiro", "contact_company": "Loja Flor"},
        )

        assert response.status_code == 201, response.text
        me = await client.get("/accounts/me", headers=auth_headers(response.json()["tokens"]))
        assert me.json()["name"] == "Ana Ribeiro"
        assert me.json()["company_name"] == "Loja Flor"
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)


async def test_draft_quote_empty_company_clears_the_detected_one(client: AsyncClient):
    """Canvas 1e "não tenho empresa — é pra mim mesmo": an explicit "" clears
    the company the conversation detected; Company.name falls back to the name."""
    _mock_butter_mind(
        _extraction(contact_email="pessoa-fisica@example.com", company_name="Detectada SA")
    )
    _mock_relay()
    try:
        conversation_id = await _run_conversation(client)

        response = await client.post(
            f"/conversations/{conversation_id}/draft-quote",
            json={"contact_name": "Bruno Alves", "contact_company": ""},
        )

        assert response.status_code == 201, response.text
        me = await client.get("/accounts/me", headers=auth_headers(response.json()["tokens"]))
        assert me.json()["company_name"] == "Bruno Alves"
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


# -- ADR-0004 turno-2: select-option / business-profile / snapshot / rate limit --


_T2_OPTIONS = [
    {
        "key": "opt_enxuto",
        "title": "Caminho enxuto",
        "summary": "Escopo mínimo.",
        "price_range": "R$ 8k–15k",
        "timeline": "4–6 semanas",
        "recommended": True,
    },
    {
        "key": "opt_completo",
        "title": "Caminho completo",
        "summary": "Escopo amplo.",
        "price_range": "R$ 20k–35k",
        "timeline": "8–12 semanas",
        "recommended": False,
    },
]


async def test_select_option_and_business_profile_relay_and_persist(client: AsyncClient):
    _mock_butter_mind(
        _extraction(),
        turn_preview=_preview(stage="choosing", options=_T2_OPTIONS, next_step=None),
    )
    try:
        start = await client.post("/conversations")
        conversation_id = start.json()["id"]
        await client.post(
            f"/conversations/{conversation_id}/messages", json={"content": "oi"}
        )

        chosen = await client.post(
            f"/conversations/{conversation_id}/select-option",
            json={"option_key": "opt_enxuto"},
        )
        assert chosen.status_code == 201, chosen.text
        assert chosen.json()["preview"]["stage"] == "ready"
        assert chosen.json()["preview"]["selected_option_key"] == "opt_enxuto"
        assert chosen.json()["ready_to_submit"] is True

        patched = await client.patch(
            f"/conversations/{conversation_id}/business-profile",
            json={"business_name": "Loja Flor", "segment": "moda", "contact_name": "Ana"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["preview"]["missing_fields"] == []

        resumed = await client.get(f"/conversations/{conversation_id}")
        turns = resumed.json()["transcript"]
        assert any(t.get("selected_option_key") == "opt_enxuto" for t in turns)
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)


async def test_draft_quote_persists_selected_option_and_profile_snapshot(
    client: AsyncClient, db_session: AsyncSession
):
    snapshot = {"segment": "moda / varejo", "city": "Curitiba PR", "volume": "~150/dia"}
    _mock_butter_mind(
        _extraction(
            contact_email="snap@example.com",
            selected_option_key="opt_enxuto",
            business_profile=snapshot,
        )
    )
    _mock_relay()
    try:
        conversation_id = await _run_conversation(client)
        response = await client.post(f"/conversations/{conversation_id}/draft-quote")
        assert response.status_code == 201, response.text
        quote_id = response.json()["quote_id"]

        quote = await QuoteRepository(db_session).get(uuid.UUID(quote_id))
        assert quote.selected_option_key == "opt_enxuto"
        assert quote.business_profile_snapshot["city"] == "Curitiba PR"
    finally:
        app.dependency_overrides.pop(get_butter_mind_client, None)
        app.dependency_overrides.pop(get_relay_client, None)


async def test_conversation_rate_limit_returns_429(client: AsyncClient):
    from app.settings.config import settings as _settings

    _mock_butter_mind(_extraction())
    _settings.conversation_rate_limit_per_minute = 3
    try:
        codes = [
            (await client.post("/conversations")).status_code for _ in range(5)
        ]
        assert codes[:3] == [201, 201, 201]
        assert 429 in codes[3:]
    finally:
        _settings.conversation_rate_limit_per_minute = 0
        app.dependency_overrides.pop(get_butter_mind_client, None)
