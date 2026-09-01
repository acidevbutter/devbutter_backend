from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import AsaasSubaccountLink, Company, User
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


# -- ACCOUNT_STATUS_* events are accepted and audited, never acted on -----
# (subaccount credential lifecycle moved to the asaas_payment proxy)


async def test_account_status_event_is_accepted_but_noop(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)
    await _create_link(db_session, "acc_webhook_status")

    response = await client.post(
        "/webhooks/asaas",
        json={
            "event": "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON",
            "account": {"id": "acc_webhook_status"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )

    assert response.status_code == 204
