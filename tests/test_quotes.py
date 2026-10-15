from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import User
from app.core.clients.asaas_client import get_asaas_client
from app.core.clients.relay_client import get_relay_client
from app.main import app
from tests.conftest import auth_headers, signup


class _FakeAsaasClient:
    async def create_customer(self, payload):
        return {"id": "cus_price_test"}

    async def create_payment(self, payload):
        return {
            "id": "pay_price_test",
            "status": "PENDING",
            "invoiceUrl": "https://asaas.test/i/pay_price_test",
        }


class _FakeRelayClient:
    def send(self, template_key, to, payload, project_id=None, queue=None):
        pass


async def _make_admin(db_session: AsyncSession, email: str) -> None:
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.is_admin = True
    await db_session.commit()


async def test_client_facing_get_quote_hides_price_range_while_draft(
    client: AsyncClient, db_session: AsyncSession
):
    client_tokens = await signup(client, email="draft-price-client@example.com")
    admin_tokens = await signup(client, email="draft-price-admin@example.com")
    await _make_admin(db_session, "draft-price-admin@example.com")

    me_response = await client.get("/accounts/me", headers=auth_headers(client_tokens))
    account_id = me_response.json()["id"]

    quote_response = await client.post(
        "/quotes/manual",
        json={
            "account_id": account_id,
            "scope_summary": "Site institucional",
            "price_range": "R$5.000 - R$8.000",
        },
        headers=auth_headers(admin_tokens),
    )
    assert quote_response.status_code == 201
    quote_id = quote_response.json()["id"]

    get_response = await client.get(f"/quotes/{quote_id}", headers=auth_headers(client_tokens))
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "draft"
    assert get_response.json()["price_range"] is None

    list_response = await client.get("/quotes", headers=auth_headers(client_tokens))
    assert list_response.status_code == 200
    assert list_response.json()[0]["price_range"] is None


async def test_client_facing_get_quote_shows_approved_invoice_amount_not_ai_price_range(
    client: AsyncClient, db_session: AsyncSession
):
    client_tokens = await signup(client, email="approved-price-client@example.com")
    admin_tokens = await signup(client, email="approved-price-admin@example.com")
    await _make_admin(db_session, "approved-price-admin@example.com")

    app.dependency_overrides[get_asaas_client] = lambda: _FakeAsaasClient()
    app.dependency_overrides[get_relay_client] = lambda: _FakeRelayClient()
    try:
        me_response = await client.get("/accounts/me", headers=auth_headers(client_tokens))
        account_id = me_response.json()["id"]

        quote_response = await client.post(
            "/quotes/manual",
            json={
                "account_id": account_id,
                "scope_summary": "Plataforma de agendamento",
                "price_range": "R$5.000 - R$8.000",
            },
            headers=auth_headers(admin_tokens),
        )
        quote_id = quote_response.json()["id"]

        approve_response = await client.post(
            f"/admin/quotes/{quote_id}/approve",
            json={
                "amount": "6500.00",
                "due_date": (date.today() + timedelta(days=7)).isoformat(),
            },
            headers=auth_headers(admin_tokens),
        )
        assert approve_response.status_code == 201, approve_response.text

        get_response = await client.get(f"/quotes/{quote_id}", headers=auth_headers(client_tokens))
        assert get_response.status_code == 200
        body = get_response.json()
        assert body["status"] == "approved"
        # The value shown is what Marcos actually approved (Invoice.amount),
        # not the AI's original draft guess ("R$5.000 - R$8.000").
        assert body["price_range"] == "6500.00"
    finally:
        app.dependency_overrides.pop(get_asaas_client, None)
        app.dependency_overrides.pop(get_relay_client, None)


async def test_admin_quotes_routes_always_show_price_range_regardless_of_status(
    client: AsyncClient, db_session: AsyncSession
):
    client_tokens = await signup(client, email="admin-view-client@example.com")
    admin_tokens = await signup(client, email="admin-view-admin@example.com")
    await _make_admin(db_session, "admin-view-admin@example.com")

    me_response = await client.get("/accounts/me", headers=auth_headers(client_tokens))
    account_id = me_response.json()["id"]

    quote_response = await client.post(
        "/quotes/manual",
        json={
            "account_id": account_id,
            "scope_summary": "Automação de leads",
            "price_range": "R$3.000 - R$4.000",
        },
        headers=auth_headers(admin_tokens),
    )
    quote_id = quote_response.json()["id"]

    review_queue = await client.get("/admin/quotes", headers=auth_headers(admin_tokens))
    assert review_queue.status_code == 200
    reviewed = next(q for q in review_queue.json() if q["id"] == quote_id)
    assert reviewed["price_range"] == "R$3.000 - R$4.000"

    all_quotes = await client.get("/admin/quotes/all", headers=auth_headers(admin_tokens))
    assert all_quotes.status_code == 200
    listed = next(q for q in all_quotes.json()["items"] if q["id"] == quote_id)
    assert listed["price_range"] == "R$3.000 - R$4.000"


async def test_admin_edit_quote_round_trips_deliverables_and_internal_notes(
    client: AsyncClient, db_session: AsyncSession
):
    client_tokens = await signup(client, email="deliverables-client@example.com")
    admin_tokens = await signup(client, email="deliverables-admin@example.com")
    await _make_admin(db_session, "deliverables-admin@example.com")

    # The PATCH /admin/quotes/{id} route resolves the full AdminService, which
    # pulls in the Asaas + relay clients -- stub them like the approve test.
    app.dependency_overrides[get_asaas_client] = lambda: _FakeAsaasClient()
    app.dependency_overrides[get_relay_client] = lambda: _FakeRelayClient()
    try:
        me_response = await client.get("/accounts/me", headers=auth_headers(client_tokens))
        account_id = me_response.json()["id"]

        quote_response = await client.post(
            "/quotes/manual",
            json={
                "account_id": account_id,
                "scope_summary": "Loja + agente",
                "price_range": "18000-24000",
            },
            headers=auth_headers(admin_tokens),
        )
        quote_id = quote_response.json()["id"]
        # New fields default to an empty list / null before anyone edits them.
        assert quote_response.json()["deliverables"] == []
        assert quote_response.json()["internal_notes"] is None

        edit_response = await client.patch(
            f"/admin/quotes/{quote_id}",
            json={
                "deliverables": ["Design e front da loja", "Agente RAG + WhatsApp"],
                "internal_notes": "Cliente sensível a prazo.",
            },
            headers=auth_headers(admin_tokens),
        )
        assert edit_response.status_code == 200, edit_response.text
        body = edit_response.json()
        assert body["deliverables"] == ["Design e front da loja", "Agente RAG + WhatsApp"]
        assert body["internal_notes"] == "Cliente sensível a prazo."

        # ...and they survive a re-read of the admin list.
        all_quotes = await client.get("/admin/quotes/all", headers=auth_headers(admin_tokens))
        listed = next(q for q in all_quotes.json()["items"] if q["id"] == quote_id)
        assert listed["deliverables"] == ["Design e front da loja", "Agente RAG + WhatsApp"]
        assert listed["internal_notes"] == "Cliente sensível a prazo."
    finally:
        app.dependency_overrides.pop(get_asaas_client, None)
        app.dependency_overrides.pop(get_relay_client, None)
