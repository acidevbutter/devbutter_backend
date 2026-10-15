import hashlib
import json
from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import User
from app.core.clients.asaas_client import get_asaas_client
from app.core.clients.relay_client import get_relay_client
from app.invoices.models import Invoice
from app.main import app
from app.settings.config import settings
from tests.conftest import auth_headers, signup


class FakeAsaasClient:
    def __init__(self):
        self.created_customers = []
        self.created_payments = []

    async def create_customer(self, payload):
        self.created_customers.append(payload)
        return {"id": "cus_000001"}

    async def create_payment(self, payload):
        self.created_payments.append(payload)
        return {
            "id": "pay_000001",
            "status": "PENDING",
            "invoiceUrl": "https://asaas.test/i/pay_000001",
        }


class FakeRelayClient:
    def __init__(self):
        self.sent = []

    def send(self, template_key, to, payload, project_id=None, queue=None):
        self.sent.append((template_key, to, payload))


async def _make_admin(db_session: AsyncSession, email: str) -> None:
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.is_admin = True
    await db_session.commit()


async def test_approve_quote_creates_order_customer_link_and_invoice(
    client: AsyncClient, db_session: AsyncSession
):
    client_tokens = await signup(client, email="client@example.com", cpf="12345678900")
    admin_tokens = await signup(client, email="marcos@example.com")
    await _make_admin(db_session, "marcos@example.com")

    fake_asaas = FakeAsaasClient()
    fake_relay = FakeRelayClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_relay_client] = lambda: fake_relay

    me_response = await client.get("/accounts/me", headers=auth_headers(client_tokens))
    client_account_id = me_response.json()["id"]

    quote_response = await client.post(
        "/quotes/manual",
        json={
            "account_id": client_account_id,
            "scope_summary": "Site institucional + automação de leads",
            "price_range": "R$5.000 - R$8.000",
            "timeline": "4 semanas",
        },
        headers=auth_headers(admin_tokens),
    )
    assert quote_response.status_code == 201
    quote_id = quote_response.json()["id"]

    approve_response = await client.post(
        f"/admin/quotes/{quote_id}/approve",
        json={"amount": "6000.00", "due_date": (date.today() + timedelta(days=7)).isoformat()},
        headers=auth_headers(admin_tokens),
    )

    del app.dependency_overrides[get_asaas_client]
    del app.dependency_overrides[get_relay_client]

    assert approve_response.status_code == 201, approve_response.text
    order = approve_response.json()
    assert order["status"] == "invoiced"

    assert len(fake_asaas.created_customers) == 1
    assert len(fake_asaas.created_payments) == 1
    assert fake_asaas.created_payments[0]["value"] == 6000.0

    assert len(fake_relay.sent) == 1
    template_key, to, _payload = fake_relay.sent[0]
    assert template_key == "devbutter_quote_approved"
    assert to == "client@example.com"


async def test_approving_already_approved_quote_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    client_tokens = await signup(client, email="client2@example.com", cpf="12345678900")
    admin_tokens = await signup(client, email="marcos2@example.com")
    await _make_admin(db_session, "marcos2@example.com")

    fake_asaas = FakeAsaasClient()
    fake_relay = FakeRelayClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_relay_client] = lambda: fake_relay

    me_response = await client.get("/accounts/me", headers=auth_headers(client_tokens))
    client_account_id = me_response.json()["id"]

    quote_response = await client.post(
        "/quotes/manual",
        json={"account_id": client_account_id, "scope_summary": "Escopo"},
        headers=auth_headers(admin_tokens),
    )
    quote_id = quote_response.json()["id"]

    approval_body = {
        "amount": "1000.00",
        "due_date": (date.today() + timedelta(days=7)).isoformat(),
    }
    first = await client.post(
        f"/admin/quotes/{quote_id}/approve", json=approval_body, headers=auth_headers(admin_tokens)
    )
    assert first.status_code == 201

    second = await client.post(
        f"/admin/quotes/{quote_id}/approve", json=approval_body, headers=auth_headers(admin_tokens)
    )

    del app.dependency_overrides[get_asaas_client]
    del app.dependency_overrides[get_relay_client]

    assert second.status_code == 409


async def test_non_admin_cannot_approve_quotes(client: AsyncClient):
    client_tokens = await signup(client, email="client3@example.com")
    response = await client.post(
        "/admin/quotes/00000000-0000-0000-0000-000000000000/approve",
        json={"amount": "100.00", "due_date": date.today().isoformat()},
        headers=auth_headers(client_tokens),
    )
    assert response.status_code == 403


async def test_asaas_webhook_updates_invoice_status_idempotently(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", "test-webhook-token")

    client_tokens = await signup(client, email="webhook-client@example.com", cpf="12345678900")
    admin_tokens = await signup(client, email="marcos3@example.com")
    await _make_admin(db_session, "marcos3@example.com")

    fake_asaas = FakeAsaasClient()
    fake_relay = FakeRelayClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_relay_client] = lambda: fake_relay

    me_response = await client.get("/accounts/me", headers=auth_headers(client_tokens))
    client_account_id = me_response.json()["id"]

    quote_response = await client.post(
        "/quotes/manual",
        json={"account_id": client_account_id, "scope_summary": "Escopo"},
        headers=auth_headers(admin_tokens),
    )
    quote_id = quote_response.json()["id"]

    await client.post(
        f"/admin/quotes/{quote_id}/approve",
        json={"amount": "1000.00", "due_date": (date.today() + timedelta(days=7)).isoformat()},
        headers=auth_headers(admin_tokens),
    )
    fake_relay.sent.clear()  # drop the quote-approved notification, isolate the webhook's own

    webhook_payload = {
        "event": "PAYMENT_RECEIVED",
        "payment": {"id": "pay_000001", "status": "RECEIVED"},
    }
    headers = {"asaas-access-token": "test-webhook-token"}

    first = await client.post("/webhooks/asaas", json=webhook_payload, headers=headers)
    assert first.status_code == 204
    assert len(fake_relay.sent) == 1

    second = await client.post("/webhooks/asaas", json=webhook_payload, headers=headers)
    assert second.status_code == 204
    assert len(fake_relay.sent) == 1  # unchanged: same status redelivered is a no-op

    invoice = await db_session.scalar(
        select(Invoice).where(Invoice.asaas_charge_id == "pay_000001")
    )
    assert invoice is not None
    assert invoice.last_event_at is not None
    assert invoice.provider_payload_hash == hashlib.sha256(
        json.dumps(webhook_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    events = await client.get(
        "/admin/webhooks/events?event_type=PAYMENT_RECEIVED",
        headers=auth_headers(admin_tokens),
    )
    assert events.status_code == 200
    assert events.json()["total"] == 2
    assert events.json()["error_count"] == 0
    event_id = events.json()["items"][0]["id"]

    detail = await client.get(
        f"/admin/webhooks/events/{event_id}",
        headers=auth_headers(admin_tokens),
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    # "recebido" line is always present; a processed event adds an "ok" line.
    assert detail_body["attempts"][0]["result"] == "recebido"
    assert detail_body["attempts"][-1]["result"] == "ok"
    # related_invoice_id set -> client / invoice / order correlation chips.
    kinds = {c["kind"] for c in detail_body["correlation"]}
    assert kinds == {"client", "invoice", "order"}

    reprocess = await client.post(
        f"/admin/webhooks/events/{event_id}/reprocess",
        headers=auth_headers(admin_tokens),
    )
    assert reprocess.status_code == 202
    assert reprocess.json()["processed_ok"] is True

    del app.dependency_overrides[get_asaas_client]
    del app.dependency_overrides[get_relay_client]


async def test_asaas_webhook_rejects_wrong_token(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", "test-webhook-token")
    response = await client.post(
        "/webhooks/asaas",
        json={"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_x", "status": "RECEIVED"}},
        headers={"asaas-access-token": "wrong-token"},
    )
    assert response.status_code == 401


class _FailingPaymentAsaasClient(FakeAsaasClient):
    async def create_payment(self, payload):
        from app.core.exceptions import UpstreamServiceError

        raise UpstreamServiceError("asaas_payment returned 400 for POST /payments")


async def _quote_for(client: AsyncClient, admin_tokens, client_tokens) -> str:
    me = await client.get("/accounts/me", headers=auth_headers(client_tokens))
    created = await client.post(
        "/quotes/manual",
        json={
            "account_id": me.json()["id"],
            "scope_summary": "Site institucional",
            "price_range": "R$5.000",
            "timeline": "4 semanas",
        },
        headers=auth_headers(admin_tokens),
    )
    assert created.status_code == 201
    return created.json()["id"]


def _body() -> dict:
    return {"amount": "6000.00", "due_date": (date.today() + timedelta(days=7)).isoformat()}


async def test_approve_without_cpf_cnpj_returns_422_and_touches_nothing(
    client: AsyncClient, db_session: AsyncSession
):
    client_tokens = await signup(client, email="nodoc@example.com")  # no cpf
    admin_tokens = await signup(client, email="admin1@example.com")
    await _make_admin(db_session, "admin1@example.com")
    fake_asaas = FakeAsaasClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_relay_client] = lambda: FakeRelayClient()
    try:
        quote_id = await _quote_for(client, admin_tokens, client_tokens)
        response = await client.post(
            f"/admin/quotes/{quote_id}/approve", json=_body(), headers=auth_headers(admin_tokens)
        )
        quote = await client.get(f"/quotes/{quote_id}", headers=auth_headers(admin_tokens))
    finally:
        app.dependency_overrides.pop(get_asaas_client, None)
        app.dependency_overrides.pop(get_relay_client, None)

    assert response.status_code == 422, response.text
    assert "CPF/CNPJ" in response.json()["detail"]
    assert fake_asaas.created_customers == []
    assert quote.json()["status"] == "draft"


async def test_asaas_failure_reverts_quote_and_allows_retry(
    client: AsyncClient, db_session: AsyncSession
):
    client_tokens = await signup(client, email="doc@example.com", cpf="12345678900")
    admin_tokens = await signup(client, email="admin2@example.com")
    await _make_admin(db_session, "admin2@example.com")
    app.dependency_overrides[get_relay_client] = lambda: FakeRelayClient()
    try:
        quote_id = await _quote_for(client, admin_tokens, client_tokens)
        app.dependency_overrides[get_asaas_client] = lambda: _FailingPaymentAsaasClient()
        failed = await client.post(
            f"/admin/quotes/{quote_id}/approve", json=_body(), headers=auth_headers(admin_tokens)
        )
        after_failure = await client.get(f"/quotes/{quote_id}", headers=auth_headers(admin_tokens))

        app.dependency_overrides[get_asaas_client] = lambda: FakeAsaasClient()
        retry = await client.post(
            f"/admin/quotes/{quote_id}/approve", json=_body(), headers=auth_headers(admin_tokens)
        )
    finally:
        app.dependency_overrides.pop(get_asaas_client, None)
        app.dependency_overrides.pop(get_relay_client, None)

    assert failed.status_code == 502
    assert after_failure.json()["status"] == "draft"
    assert retry.status_code == 201, retry.text


async def test_admin_fills_document_then_approves(client: AsyncClient, db_session: AsyncSession):
    client_tokens = await signup(client, email="fill@example.com")  # no cpf
    admin_tokens = await signup(client, email="admin3@example.com")
    await _make_admin(db_session, "admin3@example.com")
    app.dependency_overrides[get_asaas_client] = lambda: FakeAsaasClient()
    app.dependency_overrides[get_relay_client] = lambda: FakeRelayClient()
    try:
        quote_id = await _quote_for(client, admin_tokens, client_tokens)
        account_id = (await client.get("/accounts/me", headers=auth_headers(client_tokens))).json()[
            "id"
        ]
        bad = await client.patch(
            f"/admin/clients/{account_id}/document",
            json={"cpf": "123"},
            headers=auth_headers(admin_tokens),
        )
        ok = await client.patch(
            f"/admin/clients/{account_id}/document",
            json={"cpf": "123.456.789-00"},
            headers=auth_headers(admin_tokens),
        )
        approved = await client.post(
            f"/admin/quotes/{quote_id}/approve", json=_body(), headers=auth_headers(admin_tokens)
        )
    finally:
        app.dependency_overrides.pop(get_asaas_client, None)
        app.dependency_overrides.pop(get_relay_client, None)

    assert bad.status_code == 422
    assert ok.status_code == 200 and ok.json()["cpf"] == "12345678900"
    assert approved.status_code == 201, approved.text
