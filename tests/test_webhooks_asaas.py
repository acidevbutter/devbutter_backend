from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import (
    Account,
    AsaasSubaccountLink,
    Company,
    CompanyBillingProfile,
    User,
)
from app.accounts.repository import AsaasSubaccountLinkRepository
from app.core.clients.asaas_client import AsaasClient
from app.core.clients.relay_client import RelayClient
from app.core.exceptions import UpstreamServiceError
from app.invoices.models import Invoice
from app.orders.models import Order
from app.settings.config import settings

WEBHOOK_TOKEN = "webhook-shared-secret"


async def _create_link(db_session: AsyncSession, asaas_account_id: str) -> AsaasSubaccountLink:
    user = User(email=f"{asaas_account_id}@example.com", password_hash="x", name="Dono")
    db_session.add(user)
    await db_session.flush()
    company = Company(user_id=user.id, name="Empresa Webhook", cnpj="12345678000199")
    db_session.add(company)
    await db_session.flush()
    link = AsaasSubaccountLink(
        company_id=company.id, asaas_account_id=asaas_account_id, wallet_id="wallet_1"
    )
    db_session.add(link)
    await db_session.commit()
    await db_session.refresh(link)
    return link


async def _seed_company_with_pending_invoice(
    db_session: AsyncSession, *, asaas_invoice_id: str
) -> Company:
    """Mirrors the real flow up to the point a webhook arrives: signup
    collected a billing profile (app/accounts/billing_service.py), then
    Order approval created the Account/Order/Invoice trio
    (app/orders/service.py) with the Invoice still PENDING."""
    user = User(email=f"{uuid4().hex[:8]}@example.com", password_hash="x", name="Dona")
    db_session.add(user)
    await db_session.flush()
    company = Company(user_id=user.id, name="Loja PJ", cnpj="11222333000181")
    db_session.add(company)
    await db_session.flush()
    db_session.add(
        CompanyBillingProfile(
            company_id=company.id,
            legal_name="Loja PJ LTDA",
            billing_email="fin@loja.com",
            cnpj="11222333000181",
            company_type="LIMITED",
            mobile_phone="11999998888",
            income_value=Decimal("5000.00"),
            address="Rua A",
            address_number="10",
            province="Centro",
            postal_code="01001000",
        )
    )
    account = Account(company_id=company.id, product_name="Layer A")
    db_session.add(account)
    await db_session.flush()
    order = Order(quote_id=uuid4(), account_id=account.id)
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        Invoice(
            order_id=order.id,
            asaas_invoice_id=asaas_invoice_id,
            amount=Decimal("100.00"),
            currency="BRL",
            status="PENDING",
            due_date=date(2026, 9, 10),
        )
    )
    await db_session.commit()
    return company


# -- Entregavel 5: hmac.compare_digest regression --------------------------


async def test_webhook_rejects_missing_access_token(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)

    response = await client.post(
        "/webhooks/asaas",
        json={"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_1", "status": "RECEIVED"}},
    )

    assert response.status_code == 401


async def test_webhook_rejects_wrong_access_token(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)

    response = await client.post(
        "/webhooks/asaas",
        json={"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_1", "status": "RECEIVED"}},
        headers={"asaas-access-token": "not-the-right-token"},
    )

    assert response.status_code == 401


async def test_webhook_accepts_matching_access_token_for_unknown_payment(
    client: AsyncClient, monkeypatch
):
    # No matching Invoice exists -- confirms the request got *past* the
    # hmac.compare_digest check (a 401 would mean the fix broke valid
    # tokens) and into normal processing, which then legitimately 404s on
    # the unknown payment.
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)

    response = await client.post(
        "/webhooks/asaas",
        json={
            "event": "PAYMENT_RECEIVED",
            "payment": {"id": "pay_does_not_exist", "status": "RECEIVED"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )

    assert response.status_code == 404


# -- ACCOUNT_STATUS_* events update the local AsaasSubaccountLink mirror ---


async def test_account_status_expiring_soon_updates_link(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)
    link = await _create_link(db_session, "acc_webhook_status")

    response = await client.post(
        "/webhooks/asaas",
        json={
            "event": "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON",
            "account": {"id": "acc_webhook_status"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )

    assert response.status_code == 204
    await db_session.refresh(link)
    assert link.onboarding_status == "COMMERCIAL_INFO_EXPIRING"
    assert link.commercial_info_status == "EXPIRING_SOON"


async def test_account_status_general_approval_approved_clears_gate(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)
    link = await _create_link(db_session, "acc_approved")
    link.provisioning_status = "PROVISIONED"
    await db_session.commit()

    response = await client.post(
        "/webhooks/asaas",
        json={
            "id": "evt_approved_1",
            "event": "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED",
            "account": {"id": "acc_approved"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )
    assert response.status_code == 204
    await db_session.refresh(link)
    assert link.approval_status == "APPROVED"
    assert link.onboarding_status == "APPROVED"

    # redelivery of the same event id is a no-op
    again = await client.post(
        "/webhooks/asaas",
        json={
            "id": "evt_approved_1",
            "event": "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED",
            "account": {"id": "acc_approved"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )
    assert again.status_code == 204


# -- First paid invoice provisions the Asaas subaccount (adr-0002) ---------


async def test_first_paid_invoice_provisions_subaccount(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)
    monkeypatch.setattr(settings, "backend_public_base_url", "https://api.devbutter.test")
    # handle_payment_event() also notifies via RelayClient (real SQS in prod);
    # no queue is running for this test suite.
    monkeypatch.setattr(RelayClient, "send", lambda self, *args, **kwargs: None)

    created_bodies: list[dict] = []

    async def fake_create_subaccount(self, payload):
        created_bodies.append(payload)
        return {"id": "acc_new_1", "walletId": "wallet_new_1"}

    monkeypatch.setattr(AsaasClient, "create_subaccount", fake_create_subaccount)

    company = await _seed_company_with_pending_invoice(db_session, asaas_invoice_id="pay_first_1")

    response = await client.post(
        "/webhooks/asaas",
        json={
            "id": "evt_pay_first_1",
            "event": "PAYMENT_RECEIVED",
            "payment": {"id": "pay_first_1", "status": "RECEIVED"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )
    assert response.status_code == 204

    link = await AsaasSubaccountLinkRepository(db_session).get_by_company(company.id)
    assert link is not None
    assert link.asaas_account_id == "acc_new_1"
    assert link.wallet_id == "wallet_new_1"
    assert link.provisioning_status == "PROVISIONED"
    assert len(created_bodies) == 1

    # Asaas commonly fires both RECEIVED and CONFIRMED for the same charge --
    # the second lifecycle event for the same payment must not provision a
    # second subaccount (Invoice.status is already PAID => update_status's
    # `changed` is False, short-circuiting before _maybe_provision_subaccount
    # is even reached).
    confirmed = await client.post(
        "/webhooks/asaas",
        json={
            "id": "evt_pay_first_1_confirmed",
            "event": "PAYMENT_CONFIRMED",
            "payment": {"id": "pay_first_1", "status": "CONFIRMED"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )
    assert confirmed.status_code == 204
    assert len(created_bodies) == 1

    # Exact redelivery (same event id, "at least once" delivery) is also a
    # no-op -- caught even earlier, by process_event's own event-id dedup.
    redelivered = await client.post(
        "/webhooks/asaas",
        json={
            "id": "evt_pay_first_1",
            "event": "PAYMENT_RECEIVED",
            "payment": {"id": "pay_first_1", "status": "RECEIVED"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )
    assert redelivered.status_code == 204
    assert len(created_bodies) == 1

    link_after = await AsaasSubaccountLinkRepository(db_session).get_by_company(company.id)
    assert link_after.asaas_account_id == "acc_new_1"


async def test_second_paid_invoice_does_not_reprovision(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A Company's *second* Layer A invoice being paid must not attempt
    provisioning again -- covered independently of the link already
    existing, via InvoiceRepository.count_paid_invoices_for_company."""
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)
    monkeypatch.setattr(settings, "backend_public_base_url", "https://api.devbutter.test")
    # handle_payment_event() also notifies via RelayClient (real SQS in prod);
    # no queue is running for this test suite.
    monkeypatch.setattr(RelayClient, "send", lambda self, *args, **kwargs: None)

    created_bodies: list[dict] = []

    async def fake_create_subaccount(self, payload):
        created_bodies.append(payload)
        return {"id": "acc_new_2", "walletId": "wallet_new_2"}

    monkeypatch.setattr(AsaasClient, "create_subaccount", fake_create_subaccount)

    company = await _seed_company_with_pending_invoice(db_session, asaas_invoice_id="pay_a")
    account = (await db_session.execute(select(Account))).scalars().one()
    order2 = Order(quote_id=uuid4(), account_id=account.id)
    db_session.add(order2)
    await db_session.flush()
    db_session.add(
        Invoice(
            order_id=order2.id,
            asaas_invoice_id="pay_b",
            amount=Decimal("100.00"),
            currency="BRL",
            status="PENDING",
            due_date=date(2026, 9, 10),
        )
    )
    await db_session.commit()

    first = await client.post(
        "/webhooks/asaas",
        json={
            "id": "evt_pay_a",
            "event": "PAYMENT_RECEIVED",
            "payment": {"id": "pay_a", "status": "RECEIVED"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )
    assert first.status_code == 204
    assert len(created_bodies) == 1

    second = await client.post(
        "/webhooks/asaas",
        json={
            "id": "evt_pay_b",
            "event": "PAYMENT_RECEIVED",
            "payment": {"id": "pay_b", "status": "RECEIVED"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )
    assert second.status_code == 204
    assert len(created_bodies) == 1

    link = await AsaasSubaccountLinkRepository(db_session).get_by_company(company.id)
    assert link.asaas_account_id == "acc_new_2"


async def test_subaccount_provisioning_failure_does_not_lose_payment(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A provisioning failure must never regress the Invoice or drop the
    webhook -- it's recorded (provisioning_status=CREATION_FAILED,
    last_error) and left reprocessable (e.g. via the admin "Empresas"
    retry action), while the payment itself is already durably PAID."""
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)
    monkeypatch.setattr(settings, "backend_public_base_url", "https://api.devbutter.test")
    # handle_payment_event() also notifies via RelayClient (real SQS in prod);
    # no queue is running for this test suite.
    monkeypatch.setattr(RelayClient, "send", lambda self, *args, **kwargs: None)

    async def failing_create_subaccount(self, payload):
        raise UpstreamServiceError("asaas_payment returned 400")

    monkeypatch.setattr(AsaasClient, "create_subaccount", failing_create_subaccount)

    company = await _seed_company_with_pending_invoice(db_session, asaas_invoice_id="pay_fail_1")

    response = await client.post(
        "/webhooks/asaas",
        json={
            "id": "evt_pay_fail_1",
            "event": "PAYMENT_RECEIVED",
            "payment": {"id": "pay_fail_1", "status": "RECEIVED"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )
    # The webhook itself must still succeed -- a provisioning failure is not
    # a webhook-processing failure.
    assert response.status_code == 204

    invoice = (
        await db_session.execute(select(Invoice).where(Invoice.asaas_invoice_id == "pay_fail_1"))
    ).scalar_one()
    assert invoice.status == "PAID"

    link = await AsaasSubaccountLinkRepository(db_session).get_by_company(company.id)
    assert link is not None
    assert link.provisioning_status == "CREATION_FAILED"
    assert link.last_error
