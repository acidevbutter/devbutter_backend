from __future__ import annotations

import asyncio
import json
import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import AsaasSubaccountLink, Company, User
from app.core.clients.asaas_client import get_asaas_client
from app.core.exceptions import ConflictError
from app.integrations import hmac_signing
from app.integrations.selolibertaria_client import get_selolibertaria_client
from app.main import app
from app.settings.config import settings
from app.webhooks.repository import WebhookEventRepository

SECRET = "reactive-test-secret"


class _FakeAsaasClient:
    def __init__(self):
        self.create_access_token_calls: list[tuple[str, dict]] = []

    async def create_access_token(self, account_id, payload):
        self.create_access_token_calls.append((account_id, payload))
        return {"id": "tok_reactive", "apiKey": "rotated-via-reactive-endpoint"}

    async def list_access_tokens(self, account_id):  # pragma: no cover - unused here
        return {"data": []}


class _FakeSelolibertariaClient:
    def __init__(self):
        self.key_updated_calls: list[tuple[str, dict]] = []

    async def send_key_updated(self, asaas_account_id, payload):
        self.key_updated_calls.append((asaas_account_id, payload))
        from app.integrations.selolibertaria_client import RelayResult

        return RelayResult(status="acked", status_code=200)

    async def send_subaccount_provisioned(self, payload):  # pragma: no cover - unused here
        raise AssertionError("not expected on the reactive path")


async def _create_link(db_session: AsyncSession, asaas_account_id: str) -> AsaasSubaccountLink:
    user = User(email=f"{asaas_account_id}@example.com", password_hash="x", name="Dono")
    db_session.add(user)
    await db_session.flush()
    company = Company(user_id=user.id, name="Empresa Reativa", cnpj="12345678000199")
    db_session.add(company)
    await db_session.flush()
    link = AsaasSubaccountLink(
        company_id=company.id,
        asaas_account_id=asaas_account_id,
        wallet_id="wallet_1",
        access_token_id="tok_old",
        key_version=1,
    )
    db_session.add(link)
    await db_session.commit()
    await db_session.refresh(link)
    return link


def _signed_headers(body: bytes, *, secret: str = SECRET) -> dict[str, str]:
    signed = hmac_signing.sign(secret, body)
    return {
        "X-Selolibertaria-Signature": signed.signature,
        "X-Selolibertaria-Timestamp": signed.timestamp,
        "X-Selolibertaria-Nonce": signed.nonce,
        "Content-Type": "application/json",
    }


async def test_reactive_rotate_key_happy_path(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "devbutter_selolibertaria_relay_hmac_secret", SECRET)
    link = await _create_link(db_session, "acc_reactive_1")
    fake_asaas = _FakeAsaasClient()
    fake_relay = _FakeSelolibertariaClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_selolibertaria_client] = lambda: fake_relay

    body = json.dumps(
        {"event_id": str(uuid.uuid4()), "reason": "unauthorized", "last_verified_at": None}
    ).encode()

    response = await client.post(
        f"/integrations/selolibertaria/subaccounts/{link.asaas_account_id}/rotate-key",
        content=body,
        headers=_signed_headers(body),
    )

    assert response.status_code == 202
    assert response.json() == {"received": True}
    assert fake_asaas.create_access_token_calls[0][0] == "acc_reactive_1"
    assert fake_relay.key_updated_calls[0][1]["reason"] == "reactive"

    await db_session.refresh(link)
    assert link.selolibertaria_relay_status == "acked"
    assert link.access_token_id == "tok_reactive"

    app.dependency_overrides.pop(get_asaas_client, None)
    app.dependency_overrides.pop(get_selolibertaria_client, None)


async def test_reactive_rotate_key_rejects_invalid_signature(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "devbutter_selolibertaria_relay_hmac_secret", SECRET)
    link = await _create_link(db_session, "acc_reactive_bad_sig")
    fake_asaas = _FakeAsaasClient()
    fake_relay = _FakeSelolibertariaClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_selolibertaria_client] = lambda: fake_relay

    body = json.dumps({"event_id": str(uuid.uuid4()), "reason": "unauthorized"}).encode()
    headers = _signed_headers(body, secret="wrong-secret")

    response = await client.post(
        f"/integrations/selolibertaria/subaccounts/{link.asaas_account_id}/rotate-key",
        content=body,
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_signature"}
    assert fake_asaas.create_access_token_calls == []

    app.dependency_overrides.pop(get_asaas_client, None)
    app.dependency_overrides.pop(get_selolibertaria_client, None)


async def test_reactive_rotate_key_unknown_subaccount_is_404(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "devbutter_selolibertaria_relay_hmac_secret", SECRET)
    fake_asaas = _FakeAsaasClient()
    fake_relay = _FakeSelolibertariaClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_selolibertaria_client] = lambda: fake_relay

    body = json.dumps({"event_id": str(uuid.uuid4()), "reason": "unauthorized"}).encode()

    response = await client.post(
        "/integrations/selolibertaria/subaccounts/does-not-exist/rotate-key",
        content=body,
        headers=_signed_headers(body),
    )

    assert response.status_code == 404

    app.dependency_overrides.pop(get_asaas_client, None)
    app.dependency_overrides.pop(get_selolibertaria_client, None)


async def test_reactive_rotate_key_same_event_id_is_a_no_op(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Same event_id reprocessed must be a no-op (CONTRATO-HMAC.md)."""
    monkeypatch.setattr(settings, "devbutter_selolibertaria_relay_hmac_secret", SECRET)
    link = await _create_link(db_session, "acc_reactive_idem")
    fake_asaas = _FakeAsaasClient()
    fake_relay = _FakeSelolibertariaClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_selolibertaria_client] = lambda: fake_relay

    event_id = str(uuid.uuid4())
    body = json.dumps({"event_id": event_id, "reason": "unauthorized"}).encode()

    first = await client.post(
        f"/integrations/selolibertaria/subaccounts/{link.asaas_account_id}/rotate-key",
        content=body,
        headers=_signed_headers(body),
    )
    assert first.status_code == 202
    assert len(fake_asaas.create_access_token_calls) == 1

    second = await client.post(
        f"/integrations/selolibertaria/subaccounts/{link.asaas_account_id}/rotate-key",
        content=body,
        headers=_signed_headers(body),
    )
    assert second.status_code == 202
    assert len(fake_asaas.create_access_token_calls) == 1  # not called again

    app.dependency_overrides.pop(get_asaas_client, None)
    app.dependency_overrides.pop(get_selolibertaria_client, None)


async def test_reactive_rotate_key_concurrent_same_event_id_is_deduped(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Two genuinely concurrent requests with the same event_id must not
    both trigger a rotation, and must leave exactly one audit row --
    docs/auditoria-seguranca-2026-08-29.md item 2 / check-then-create race.
    """
    monkeypatch.setattr(settings, "devbutter_selolibertaria_relay_hmac_secret", SECRET)
    link = await _create_link(db_session, "acc_reactive_concurrent")
    fake_asaas = _FakeAsaasClient()
    fake_relay = _FakeSelolibertariaClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_selolibertaria_client] = lambda: fake_relay

    event_id = str(uuid.uuid4())
    body = json.dumps({"event_id": event_id, "reason": "unauthorized"}).encode()
    url = f"/integrations/selolibertaria/subaccounts/{link.asaas_account_id}/rotate-key"

    responses = await asyncio.gather(
        client.post(url, content=body, headers=_signed_headers(body)),
        client.post(url, content=body, headers=_signed_headers(body)),
    )

    assert all(r.status_code == 202 for r in responses)
    assert all(r.json() == {"received": True} for r in responses)
    # Exactly one rotation actually happened -- the loser of the race
    # returned the no-op response without calling create_access_token again.
    assert len(fake_asaas.create_access_token_calls) == 1

    event_repository = WebhookEventRepository(db_session)
    matching = [
        event
        for event in (await event_repository.list(event_type=None, has_error=None, offset=0, limit=100))[0]
        if event.event_id == event_id
    ]
    assert len(matching) == 1

    app.dependency_overrides.pop(get_asaas_client, None)
    app.dependency_overrides.pop(get_selolibertaria_client, None)


async def test_reactive_rotate_key_without_event_id_still_audits_and_rotates(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """No event_id in the body must not skip the audit row nor the rotation
    -- docs/auditoria-seguranca-2026-08-29.md item 2.
    """
    monkeypatch.setattr(settings, "devbutter_selolibertaria_relay_hmac_secret", SECRET)
    link = await _create_link(db_session, "acc_reactive_no_event_id")
    fake_asaas = _FakeAsaasClient()
    fake_relay = _FakeSelolibertariaClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake_asaas
    app.dependency_overrides[get_selolibertaria_client] = lambda: fake_relay

    body = json.dumps({"reason": "unauthorized"}).encode()

    response = await client.post(
        f"/integrations/selolibertaria/subaccounts/{link.asaas_account_id}/rotate-key",
        content=body,
        headers=_signed_headers(body),
    )

    assert response.status_code == 202
    assert response.json() == {"received": True}
    assert fake_asaas.create_access_token_calls[0][0] == "acc_reactive_no_event_id"

    event_repository = WebhookEventRepository(db_session)
    events, total, _ = await event_repository.list(event_type=None, has_error=None, offset=0, limit=100)
    matching = [
        event
        for event in events
        if event.event_type == "selolibertaria.rotate_key_request"
        and event.payload.get("asaas_account_id") == "acc_reactive_no_event_id"
    ]
    assert len(matching) == 1
    assert matching[0].event_id  # generated locally, not empty

    app.dependency_overrides.pop(get_asaas_client, None)
    app.dependency_overrides.pop(get_selolibertaria_client, None)


async def test_reactive_rotate_key_malformed_timestamp_header_is_401(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "devbutter_selolibertaria_relay_hmac_secret", SECRET)
    link = await _create_link(db_session, "acc_reactive_bad_timestamp")

    body = json.dumps({"event_id": str(uuid.uuid4()), "reason": "unauthorized"}).encode()
    headers = _signed_headers(body)
    headers["X-Selolibertaria-Timestamp"] = "not-a-number"

    response = await client.post(
        f"/integrations/selolibertaria/subaccounts/{link.asaas_account_id}/rotate-key",
        content=body,
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_signature"}


async def test_webhook_event_repository_create_same_event_id_raises_conflict(
    db_session: AsyncSession,
):
    """Unit-level proof of the fix: the DB unique constraint on event_id is
    surfaced as ConflictError, not a raw IntegrityError/500.
    """
    repository = WebhookEventRepository(db_session)
    event_id = str(uuid.uuid4())

    await repository.create(
        event_type="selolibertaria.rotate_key_request",
        related_invoice_id=None,
        payload={"event_id": event_id},
        event_id=event_id,
    )

    try:
        await repository.create(
            event_type="selolibertaria.rotate_key_request",
            related_invoice_id=None,
            payload={"event_id": event_id},
            event_id=event_id,
        )
        raise AssertionError("expected ConflictError")
    except ConflictError:
        pass
