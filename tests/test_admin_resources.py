from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, User
from app.conversations.models import Conversation
from app.invoices.models import Invoice
from app.orders.models import Order
from app.quotes.models import Quote
from tests.conftest import auth_headers, signup


async def _make_admin(db_session: AsyncSession, user: User) -> None:
    user.is_admin = True
    await db_session.commit()


async def test_admin_cross_account_views_and_account_invoices(
    client: AsyncClient, db_session: AsyncSession
):
    client_tokens = await signup(
        client, email="views-client@example.com", name="Cliente Views", company_name="Views Ltda."
    )
    admin_tokens = await signup(client, email="views-admin@example.com")
    # Resolve the admin through the authenticated account response so this test
    # does not depend on token internals.
    admin_me = await client.get("/accounts/me", headers=auth_headers(admin_tokens))
    admin_user = await db_session.get(User, UUID(admin_me.json()["user_id"]))
    assert admin_user is not None
    await _make_admin(db_session, admin_user)
    admin = await db_session.get(Account, UUID(admin_me.json()["id"]))
    assert admin is not None

    client_me = await client.get("/accounts/me", headers=auth_headers(client_tokens))
    client_id = UUID(client_me.json()["id"])

    conversation = Conversation(
        account_id=client_id,
        mind_session_id="mind_views",
        transcript=[
            {"role": "user", "content": "Preciso de uma loja"},
            {"role": "assistant", "content": "Vou ajudar"},
        ],
    )
    db_session.add(conversation)
    await db_session.flush()
    quote = Quote(
        account_id=client_id,
        conversation_id=conversation.id,
        reference="COT-9000",
        source="ai",
        status="approved",
        scope_summary="Loja online",
        price_range="R$ 10 mil",
        timeline="6 semanas",
        reviewed_by=admin.id,
        reviewed_at=datetime.now(UTC),
    )
    db_session.add(quote)
    await db_session.flush()
    order = Order(quote_id=quote.id, account_id=client_id, status="invoiced")
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        Invoice(
            order_id=order.id,
            asaas_invoice_id="pay_views",
            amount=Decimal("1200.00"),
            currency="BRL",
            status="PENDING",
            due_date=date.today() + timedelta(days=7),
            payment_link="https://asaas.test/pay_views",
        )
    )
    await db_session.commit()

    catalog_response = await client.post(
        "/service-catalog",
        json={"type": "ecommerce", "description": "Catálogo da loja"},
        headers=auth_headers(client_tokens),
    )
    assert catalog_response.status_code == 201

    headers = auth_headers(admin_tokens)
    clients_response = await client.get("/admin/clients?page=1&page_size=10", headers=headers)
    assert clients_response.status_code == 200
    assert clients_response.json()["items"][0]["email"] in {
        "views-client@example.com",
        "views-admin@example.com",
    }

    detail_response = await client.get(f"/admin/clients/{client_id}", headers=headers)
    assert detail_response.status_code == 200
    detail_body = detail_response.json()
    assert detail_body["counts"] == {
        "quotes": 1,
        "orders": 1,
        "invoices": 1,
        "catalog_entries": 1,
        "conversations": 1,
    }
    # No Layer B subaccount linked to this company yet.
    assert detail_body["subaccount_onboarding"] is None
    assert detail_body["subaccount_approval"] is None

    orders_response = await client.get("/admin/orders", headers=headers)
    assert orders_response.status_code == 200
    order_item = orders_response.json()["items"][0]
    assert order_item["amount"] == "1200.00"
    # One PENDING invoice -> total 1, none paid, none overdue.
    assert order_item["invoices_total"] == 1
    assert order_item["invoices_paid"] == 0
    assert order_item["invoices_overdue"] == 0

    invoices_response = await client.get("/admin/invoices", headers=headers)
    assert invoices_response.status_code == 200
    assert invoices_response.json()["items"][0]["asaas_invoice_id"] == "pay_views"

    account_invoices_response = await client.get("/invoices", headers=auth_headers(client_tokens))
    assert account_invoices_response.status_code == 200
    assert account_invoices_response.json()[0]["asaas_invoice_id"] == "pay_views"

    dashboard_response = await client.get("/admin/dashboard", headers=headers)
    assert dashboard_response.status_code == 200
    assert dashboard_response.json()["active_orders"] == 1

    catalog_response = await client.get("/admin/catalog", headers=headers)
    assert catalog_response.status_code == 200
    assert catalog_response.json()["items"][0]["account_name"] == "Views Ltda."

    quotes_response = await client.get("/admin/quotes/all?status=approved", headers=headers)
    assert quotes_response.status_code == 200
    assert quotes_response.json()["items"][0]["id"] == str(quote.id)

    conversations_response = await client.get(
        "/admin/conversations?has_quote=true", headers=headers
    )
    assert conversations_response.status_code == 200
    assert conversations_response.json()["items"][0]["message_count"] == 2

    conversation_response = await client.get(
        f"/admin/conversations/{conversation.id}", headers=headers
    )
    assert conversation_response.status_code == 200
    assert conversation_response.json()["messages"][0]["role"] == "visitor"
    assert conversation_response.json()["draft"]["scope_summary"] == "Loja online"


async def test_knowledge_entries_are_admin_only_and_persistent(
    client: AsyncClient, db_session: AsyncSession
):
    user_tokens = await signup(client, email="knowledge-user@example.com")
    admin_tokens = await signup(client, email="knowledge-admin@example.com")

    unauthenticated = await client.get("/admin/knowledge-entries")
    assert unauthenticated.status_code == 401

    forbidden = await client.get(
        "/admin/knowledge-entries", headers=auth_headers(user_tokens)
    )
    assert forbidden.status_code == 403

    # The fixture database has no admin bootstrap endpoint; promote this test
    # account through the same explicit local bootstrap used by production docs.
    admin_me = await client.get("/accounts/me", headers=auth_headers(admin_tokens))
    admin = await db_session.get(User, UUID(admin_me.json()["user_id"]))
    assert admin is not None
    await _make_admin(db_session, admin)

    headers = auth_headers(admin_tokens)
    create_response = await client.post(
        "/admin/knowledge-entries",
        json={
            "name": "Website institucional",
            "description": "Site institucional responsivo",
            "price_min": "5000.00",
            "price_max": "8000.00",
            "typical_duration": "4 semanas",
            "ai_instructions": "Priorizar escopo enxuto",
        },
        headers=headers,
    )
    assert create_response.status_code == 201, create_response.text
    entry_id = create_response.json()["id"]

    list_response = await client.get("/admin/knowledge-entries", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()[0]["name"] == "Website institucional"

    update_response = await client.patch(
        f"/admin/knowledge-entries/{entry_id}",
        json={"price_max": "9000.00"},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["price_max"] == "9000.00"

    invalid_response = await client.post(
        "/admin/knowledge-entries",
        json={
            "name": "Inválido",
            "description": "Faixa inválida",
            "price_min": "10",
            "price_max": "1",
            "typical_duration": "1 dia",
            "ai_instructions": "",
        },
        headers=headers,
    )
    assert invalid_response.status_code == 422

    delete_response = await client.delete(
        f"/admin/knowledge-entries/{entry_id}", headers=headers
    )
    assert delete_response.status_code == 204
