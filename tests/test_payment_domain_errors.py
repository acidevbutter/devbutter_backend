"""Fatia 13 (docs/prompts/payment-tokenization/runs/13-fix-error-contract.md)
-- fixes for N-04 and N-05 from the fatia 11 adversarial validation
(docs/prompts/payment-tokenization/runs/11-final-validation.md).

N-05: `POST /payments` and `DELETE /payment-methods/{id}` must return
contract-07 section 5.3's stable `{"error": "<code>"}` shape and status
code for every domain-error case in that section's table, never a generic
`{"detail": "free text"}`.

N-04: `POST /payments` is documented (contract-07 section 5.1, amended this
phase) to use snake_case on the wire, matching what
`devbutter_frontend/src/lib/portal/payment-methods.ts` has always sent --
but `PaymentCreate` now also accepts camelCase additively via
`populate_by_name=True` + `Field(alias=...)`, without narrowing what
already worked.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, AsaasCustomerLink, Company, User
from app.core.clients.asaas_client import get_asaas_client
from app.invoices.models import Invoice
from app.main import app
from app.orders.models import Order
from app.payment_methods.models import PaymentMethod
from app.settings.config import settings
from tests.conftest import auth_headers, signup


class _StubAsaasClient:
    """Minimal stub for `pay()`'s call to `pay_with_saved_payment_method` --
    only the idempotency-conflict and wire-format tests need a real
    successful charge. Every other test in this file never reaches an
    Asaas call at all (the domain-error checks all fail before that), but
    `get_payment_method_service` still resolves the real `AsaasClient`
    dependency unless overridden, and constructing the real client requires
    mTLS certs this test environment doesn't have -- so this stub is wired
    in for every test here regardless of whether it is actually called."""

    async def pay_with_saved_payment_method(self, asaas_payment_id: str, token: str) -> dict:
        return {"status": "CONFIRMED"}


@pytest.fixture(autouse=True)
def _payment_settings(monkeypatch: pytest.MonkeyPatch):
    # `payment_token_encryption_key`: only exercised by the tests whose flow
    # reaches `_token_cipher()` (idempotency-conflict, wire-format), but
    # harmless to set for all -- every other case in this file fails
    # ownership/status checks earlier.
    monkeypatch.setattr(settings, "payment_token_encryption_key", Fernet.generate_key().decode())
    app.dependency_overrides[get_asaas_client] = lambda: _StubAsaasClient()
    yield
    app.dependency_overrides.pop(get_asaas_client, None)


async def _signed_up_identity(
    client: AsyncClient, db_session: AsyncSession, email: str, company_name: str
) -> tuple[dict, Account, Company]:
    tokens = await signup(client, email=email, name="Tester", company_name=company_name)
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    company = (
        await db_session.execute(select(Company).where(Company.user_id == user.id))
    ).scalar_one()
    account = (
        await db_session.execute(select(Account).where(Account.company_id == company.id))
    ).scalar_one()
    return tokens, account, company


async def _make_invoice(
    db_session: AsyncSession,
    account: Account,
    *,
    status: str = "PENDING",
) -> Invoice:
    order = Order(quote_id=uuid4(), account_id=account.id)
    db_session.add(order)
    await db_session.flush()
    invoice = Invoice(
        order_id=order.id,
        asaas_invoice_id=f"pay_test_{uuid4().hex[:12]}",
        amount=Decimal("25.90"),
        currency="BRL",
        status=status,
        due_date=date(2026, 12, 1),
    )
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)
    return invoice


async def _make_active_payment_method(
    db_session: AsyncSession, company: Company, *, asaas_customer_id: str = "cus_test0001"
) -> PaymentMethod:
    db_session.add(AsaasCustomerLink(company_id=company.id, asaas_customer_id=asaas_customer_id))
    cipher = Fernet(settings.payment_token_encryption_key.encode())
    method = PaymentMethod(
        company_id=company.id,
        asaas_customer_id=asaas_customer_id,
        provider_token_ciphertext=cipher.encrypt(b"provider-token-test"),
        token_kid="test-kid",
        brand="visa",
        last4="4242",
        expiry_month=12,
        expiry_year=2030,
    )
    db_session.add(method)
    await db_session.commit()
    await db_session.refresh(method)
    return method


# ---------------------------------------------------------------------------
# N-05: POST /payments domain errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pay_with_nonexistent_invoice_returns_invoice_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tokens, _account, _company = await _signed_up_identity(
        client, db_session, "pay-no-invoice@example.com", "No Invoice Ltda."
    )

    response = await client.post(
        "/payments",
        json={
            "invoice_id": str(uuid4()),
            "payment_method_id": str(uuid4()),
            "idempotency_key": "x" * 20,
        },
        headers=auth_headers(tokens),
    )

    assert response.status_code == 404
    assert response.json() == {"error": "invoice_not_found"}


@pytest.mark.asyncio
async def test_pay_with_another_tenants_invoice_returns_invoice_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _owner_tokens, owner_account, _owner_company = await _signed_up_identity(
        client, db_session, "invoice-owner@example.com", "Invoice Owner Ltda."
    )
    attacker_tokens, _attacker_account, _attacker_company = await _signed_up_identity(
        client, db_session, "invoice-attacker@example.com", "Invoice Attacker Ltda."
    )
    invoice = await _make_invoice(db_session, owner_account)

    response = await client.post(
        "/payments",
        json={
            "invoice_id": str(invoice.id),
            "payment_method_id": str(uuid4()),
            "idempotency_key": "x" * 20,
        },
        headers=auth_headers(attacker_tokens),
    )

    assert response.status_code == 404
    assert response.json() == {"error": "invoice_not_found"}


@pytest.mark.asyncio
async def test_pay_with_already_paid_invoice_returns_invoice_not_payable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tokens, account, _company = await _signed_up_identity(
        client, db_session, "invoice-paid@example.com", "Invoice Paid Ltda."
    )
    invoice = await _make_invoice(db_session, account, status="PAID")

    response = await client.post(
        "/payments",
        json={
            "invoice_id": str(invoice.id),
            "payment_method_id": str(uuid4()),
            "idempotency_key": "x" * 20,
        },
        headers=auth_headers(tokens),
    )

    assert response.status_code == 409
    assert response.json() == {"error": "invoice_not_payable"}


@pytest.mark.asyncio
async def test_pay_with_cancelled_invoice_returns_invoice_not_payable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tokens, account, _company = await _signed_up_identity(
        client, db_session, "invoice-cancelled@example.com", "Invoice Cancelled Ltda."
    )
    invoice = await _make_invoice(db_session, account, status="CANCELLED")

    response = await client.post(
        "/payments",
        json={
            "invoice_id": str(invoice.id),
            "payment_method_id": str(uuid4()),
            "idempotency_key": "x" * 20,
        },
        headers=auth_headers(tokens),
    )

    assert response.status_code == 409
    assert response.json() == {"error": "invoice_not_payable"}


@pytest.mark.asyncio
async def test_pay_with_nonexistent_payment_method_returns_payment_method_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tokens, account, _company = await _signed_up_identity(
        client, db_session, "pay-no-method@example.com", "No Method Ltda."
    )
    invoice = await _make_invoice(db_session, account)

    response = await client.post(
        "/payments",
        json={
            "invoice_id": str(invoice.id),
            "payment_method_id": str(uuid4()),
            "idempotency_key": "x" * 20,
        },
        headers=auth_headers(tokens),
    )

    assert response.status_code == 404
    assert response.json() == {"error": "payment_method_not_found"}


@pytest.mark.asyncio
async def test_pay_with_another_tenants_payment_method_returns_payment_method_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _owner_tokens, _owner_account, owner_company = await _signed_up_identity(
        client, db_session, "method-owner@example.com", "Method Owner Ltda."
    )
    attacker_tokens, attacker_account, _attacker_company = await _signed_up_identity(
        client, db_session, "method-attacker@example.com", "Method Attacker Ltda."
    )
    method = await _make_active_payment_method(db_session, owner_company)
    invoice = await _make_invoice(db_session, attacker_account)

    response = await client.post(
        "/payments",
        json={
            "invoice_id": str(invoice.id),
            "payment_method_id": str(method.id),
            "idempotency_key": "x" * 20,
        },
        headers=auth_headers(attacker_tokens),
    )

    assert response.status_code == 404
    assert response.json() == {"error": "payment_method_not_found"}


@pytest.mark.asyncio
async def test_pay_with_revoked_payment_method_returns_payment_method_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """This is the exact scenario the fatia 11 validation reproduced live
    against the real backend (`11-final-validation.md`, evidence #20): a
    revoked payment method used on `POST /payments` returned
    `{"detail": "Payment method not found"}` instead of the contract's
    `{"error": "payment_method_not_found"}`."""

    tokens, account, company = await _signed_up_identity(
        client, db_session, "pay-revoked-method@example.com", "Revoked Method Ltda."
    )
    method = await _make_active_payment_method(db_session, company)
    method.status = "revoked"
    await db_session.commit()
    invoice = await _make_invoice(db_session, account)

    response = await client.post(
        "/payments",
        json={
            "invoice_id": str(invoice.id),
            "payment_method_id": str(method.id),
            "idempotency_key": "x" * 20,
        },
        headers=auth_headers(tokens),
    )

    assert response.status_code == 404
    assert response.json() == {"error": "payment_method_not_found"}


@pytest.mark.asyncio
async def test_pay_with_reused_idempotency_key_returns_idempotency_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tokens, account, company = await _signed_up_identity(
        client, db_session, "idempotency-conflict@example.com", "Idempotency Ltda."
    )
    method = await _make_active_payment_method(db_session, company)
    invoice = await _make_invoice(db_session, account)

    body = {
        "invoice_id": str(invoice.id),
        "payment_method_id": str(method.id),
        "idempotency_key": "same-idempotency-key-value",
    }
    first = await client.post("/payments", json=body, headers=auth_headers(tokens))
    assert first.status_code == 202, first.text

    second = await client.post("/payments", json=body, headers=auth_headers(tokens))

    assert second.status_code == 409
    assert second.json() == {"error": "idempotency_conflict"}


# ---------------------------------------------------------------------------
# N-05: DELETE /payment-methods/{id} domain errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_nonexistent_payment_method_returns_payment_method_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tokens, _account, _company = await _signed_up_identity(
        client, db_session, "revoke-no-method@example.com", "Revoke No Method Ltda."
    )

    response = await client.delete(f"/payment-methods/{uuid4()}", headers=auth_headers(tokens))

    assert response.status_code == 404
    assert response.json() == {"error": "payment_method_not_found"}


@pytest.mark.asyncio
async def test_revoke_another_tenants_payment_method_returns_payment_method_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _owner_tokens, _owner_account, owner_company = await _signed_up_identity(
        client, db_session, "revoke-owner@example.com", "Revoke Owner Ltda."
    )
    attacker_tokens, _attacker_account, _attacker_company = await _signed_up_identity(
        client, db_session, "revoke-attacker@example.com", "Revoke Attacker Ltda."
    )
    method = await _make_active_payment_method(db_session, owner_company)

    response = await client.delete(
        f"/payment-methods/{method.id}", headers=auth_headers(attacker_tokens)
    )

    assert response.status_code == 404
    assert response.json() == {"error": "payment_method_not_found"}


# ---------------------------------------------------------------------------
# N-04: POST /payments wire-format decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pay_accepts_snake_case_body_matching_the_real_frontend(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`devbutter_frontend/src/lib/portal/payment-methods.ts`
    (`payWithPortalPaymentMethod`) sends snake_case -- this must keep
    working exactly as it does today (contract-07 section 5.1, amended)."""

    tokens, account, company = await _signed_up_identity(
        client, db_session, "wire-snake-case@example.com", "Wire Snake Ltda."
    )
    method = await _make_active_payment_method(db_session, company)
    invoice = await _make_invoice(db_session, account)

    response = await client.post(
        "/payments",
        json={
            "invoice_id": str(invoice.id),
            "payment_method_id": str(method.id),
            "idempotency_key": "snake-case-request-body",
        },
        headers=auth_headers(tokens),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    # Response stays snake_case too -- devbutter_frontend's
    # `SavedPaymentResult` type reads `provider_payment_id`, not
    # `providerPaymentId`.
    assert set(body.keys()) == {"id", "state", "provider_payment_id"}
    assert body["state"] == "succeeded"


@pytest.mark.asyncio
async def test_pay_additionally_accepts_camel_case_body(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """N-04: camelCase acceptance is purely additive (`populate_by_name=True`
    + `Field(alias=...)` on `PaymentCreate`) -- contract-07's originally
    drafted camelCase now also works, without narrowing what already did."""

    tokens, account, company = await _signed_up_identity(
        client, db_session, "wire-camel-case@example.com", "Wire Camel Ltda."
    )
    method = await _make_active_payment_method(db_session, company)
    invoice = await _make_invoice(db_session, account)

    response = await client.post(
        "/payments",
        json={
            "invoiceId": str(invoice.id),
            "paymentMethodId": str(method.id),
            "idempotencyKey": "camel-case-request-body",
        },
        headers=auth_headers(tokens),
    )

    assert response.status_code == 202, response.text
    assert response.json()["state"] == "succeeded"
