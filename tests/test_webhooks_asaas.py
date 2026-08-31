from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.clients.asaas_client as asaas_client_module
import app.integrations.selolibertaria_client as selolibertaria_client_module
from app.accounts.models import AsaasSubaccountLink, Company, User
from app.integrations.selolibertaria_client import RelayResult
from app.settings.config import settings

WEBHOOK_TOKEN = "webhook-shared-secret"


class _FakeAsaasClient:
    def __init__(self):
        self.create_access_token_calls: list[tuple[str, dict]] = []

    async def create_access_token(self, account_id, payload):
        self.create_access_token_calls.append((account_id, payload))
        return {"id": "tok_from_webhook", "apiKey": "rotated-via-webhook"}

    async def list_access_tokens(self, account_id):  # pragma: no cover - unused here
        return {"data": []}


class _FakeSelolibertariaClient:
    def __init__(self):
        self.key_updated_calls: list[tuple[str, dict]] = []

    async def send_key_updated(self, asaas_account_id, payload):
        self.key_updated_calls.append((asaas_account_id, payload))
        return RelayResult(status="acked", status_code=200)

    async def send_subaccount_provisioned(self, payload):  # pragma: no cover - unused here
        raise AssertionError("not expected on the account-status webhook path")


def _preseed_rotation_singletons(monkeypatch, fake_asaas, fake_relay):
    """Pre-seeds the module-level singleton caches
    (app.core.clients.asaas_client._client and
    app.integrations.selolibertaria_client._client) so get_asaas_client()/
    get_selolibertaria_client() return the fakes without ever constructing
    the real mTLS AsaasClient.

    Deliberately NOT app.dependency_overrides here: get_webhook_service
    (app/webhooks/router.py) calls these two getters as plain functions
    from a lazily-invoked factory, not as FastAPI Depends() -- see that
    module's docstring for why (PAYMENT_*'s hot path must never pay for
    constructing the mTLS AsaasClient). monkeypatch reverts both after each
    test automatically.
    """
    monkeypatch.setattr(asaas_client_module, "_client", fake_asaas)
    monkeypatch.setattr(selolibertaria_client_module, "_client", fake_relay)


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


# -- Entregavel 5: hmac.compare_digest regression ----------------------------
#
# These three intentionally do NOT touch AsaasClient/SelolibertariaClient at
# all -- PAYMENT_* events (and any rejected-before-processing request) never
# build a CredentialRotationService, so nothing here needs the singletons
# pre-seeded. That is itself part of what's being verified: the fix must
# not have coupled the payment webhook's hot path to the mTLS client.


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


# -- Entregavel 3 (second half): ACCOUNT_STATUS_* webhook drives rotation ---


async def test_account_status_expiring_soon_triggers_rotation(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)
    link = await _create_link(db_session, "acc_webhook_expiring")
    fake_asaas = _FakeAsaasClient()
    fake_relay = _FakeSelolibertariaClient()
    _preseed_rotation_singletons(monkeypatch, fake_asaas, fake_relay)

    response = await client.post(
        "/webhooks/asaas",
        json={
            "event": "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON",
            "account": {"id": "acc_webhook_expiring"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )

    assert response.status_code == 204
    assert fake_asaas.create_access_token_calls[0][0] == "acc_webhook_expiring"
    assert fake_relay.key_updated_calls[0][1]["reason"] == "proactive"

    await db_session.refresh(link)
    assert link.selolibertaria_relay_status == "acked"


async def test_account_status_unrelated_event_is_accepted_but_noop(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_webhook_access_token", WEBHOOK_TOKEN)
    await _create_link(db_session, "acc_webhook_other")
    fake_asaas = _FakeAsaasClient()
    fake_relay = _FakeSelolibertariaClient()
    _preseed_rotation_singletons(monkeypatch, fake_asaas, fake_relay)

    response = await client.post(
        "/webhooks/asaas",
        json={
            "event": "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_APPROVED",
            "account": {"id": "acc_webhook_other"},
        },
        headers={"asaas-access-token": WEBHOOK_TOKEN},
    )

    assert response.status_code == 204
    assert fake_asaas.create_access_token_calls == []
