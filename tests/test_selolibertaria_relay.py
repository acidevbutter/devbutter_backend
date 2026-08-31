from __future__ import annotations

import json

import httpx
import pytest

from app.accounts.models import AsaasSubaccountLink, Company, User
from app.accounts.repository import AsaasSubaccountLinkRepository
from app.core.exceptions import UpstreamServiceError
from app.integrations import hmac_signing
from app.integrations.relay_service import SelolibertariaRelayService
from app.integrations.rotation_service import CredentialRotationService
from app.integrations.selolibertaria_client import SelolibertariaClient
from app.settings.config import settings

# -- SelolibertariaClient (outbound HTTP + retry/backoff) ------------------


class _PostRecorder:
    """Stands in for httpx.AsyncClient.post -- replays queued responses, or
    raises the next queued exception (simulating Selolibertaria being
    unreachable), one per call.
    """

    def __init__(self, results: list[httpx.Response | Exception]):
        self.results = list(results)
        self.calls: list[dict] = []

    async def __call__(self, path, *, content, headers):
        self.calls.append({"path": path, "content": content, "headers": headers})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code, json={}, request=httpx.Request("POST", "https://selolibertaria/x")
    )


@pytest.fixture
def relay_client(monkeypatch) -> SelolibertariaClient:
    monkeypatch.setattr(settings, "devbutter_selolibertaria_relay_hmac_secret", "relay-secret")
    return SelolibertariaClient()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # Retry/backoff sleeps aren't the thing under test -- keep the suite fast.
    import app.integrations.selolibertaria_client as module

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", _instant)


async def test_send_subaccount_provisioned_202_is_sent(relay_client, monkeypatch):
    recorder = _PostRecorder([_response(202)])
    monkeypatch.setattr(relay_client._client, "post", recorder)

    payload = {"event_id": "evt-1", "asaas_account_id": "acc_1", "api_key": "should-not-leak"}
    result = await relay_client.send_subaccount_provisioned(payload)

    assert result.status == "sent"
    assert result.status_code == 202
    call = recorder.calls[0]
    assert call["path"] == "/integrations/devbutter/subaccounts"
    assert json.loads(call["content"]) == payload
    # Signature is verifiable with the shared secret and the exact body sent.
    assert hmac_signing.verify(
        "relay-secret",
        timestamp=call["headers"]["X-DevButter-Timestamp"],
        nonce=call["headers"]["X-DevButter-Nonce"],
        signature=call["headers"]["X-DevButter-Signature"],
        raw_body=call["content"],
    )


async def test_send_key_updated_200_is_acked(relay_client, monkeypatch):
    recorder = _PostRecorder([_response(200)])
    monkeypatch.setattr(relay_client._client, "post", recorder)

    result = await relay_client.send_key_updated(
        "acc_1", {"event_id": "evt-2", "api_key": "x", "key_version": 2, "reason": "proactive"}
    )

    assert result.status == "acked"
    assert recorder.calls[0]["path"] == "/integrations/devbutter/subaccounts/acc_1/key-updated"


async def test_delivery_retries_on_5xx_then_succeeds(relay_client, monkeypatch):
    recorder = _PostRecorder([_response(503), _response(202)])
    monkeypatch.setattr(relay_client._client, "post", recorder)

    result = await relay_client.send_subaccount_provisioned({"event_id": "evt-3"})

    assert result.status == "sent"
    assert len(recorder.calls) == 2


async def test_delivery_unreachable_selolibertaria_exhausts_retries_and_fails(
    relay_client, monkeypatch
):
    """Selolibertaria fora do ar -- every attempt raises a transport error.
    Exhausts the fixed retry budget (3 attempts) and reports `failed` so the
    caller can leave the credential queued for a later retry, rather than
    silently believing it delivered.
    """
    recorder = _PostRecorder(
        [httpx.ConnectError("connection refused"), httpx.ConnectError("connection refused"),
         httpx.ConnectError("connection refused")]
    )
    monkeypatch.setattr(relay_client._client, "post", recorder)

    result = await relay_client.send_subaccount_provisioned({"event_id": "evt-4", "api_key": "x"})

    assert result.status == "failed"
    assert len(recorder.calls) == 3
    assert result.detail is not None
    assert "x" not in result.detail  # never leaks payload content into the diagnostic


async def test_delivery_gives_up_on_4xx_without_retrying(relay_client, monkeypatch):
    recorder = _PostRecorder([_response(401)])
    monkeypatch.setattr(relay_client._client, "post", recorder)

    result = await relay_client.send_subaccount_provisioned({"event_id": "evt-5"})

    assert result.status == "failed"
    assert len(recorder.calls) == 1


# -- SelolibertariaRelayService ---------------------------------------------


class _FakeRelayClient:
    def __init__(self, result):
        self._result = result
        self.provisioned_calls: list[dict] = []
        self.key_updated_calls: list[tuple[str, dict]] = []

    async def send_subaccount_provisioned(self, payload):
        self.provisioned_calls.append(payload)
        return self._result

    async def send_key_updated(self, asaas_account_id, payload):
        self.key_updated_calls.append((asaas_account_id, payload))
        return self._result


class _Result:
    def __init__(self, status, status_code=202, detail=None):
        self.status = status
        self.status_code = status_code
        self.detail = detail


async def _make_link(db_session, *, asaas_account_id="acc_1") -> AsaasSubaccountLink:
    user = User(email=f"{asaas_account_id}@example.com", password_hash="x", name="Dono")
    db_session.add(user)
    await db_session.flush()
    company = Company(user_id=user.id, name="Empresa", cnpj="12345678000199")
    db_session.add(company)
    await db_session.flush()
    link = AsaasSubaccountLink(
        company_id=company.id, asaas_account_id=asaas_account_id, wallet_id="wallet_1"
    )
    db_session.add(link)
    await db_session.commit()
    await db_session.refresh(link)
    return link


async def test_relay_initial_provisioning_happy_path(db_session):
    link = await _make_link(db_session)
    fake_client = _FakeRelayClient(_Result("sent", 202))
    service = SelolibertariaRelayService(fake_client, AsaasSubaccountLinkRepository(db_session))

    updated = await service.relay_initial_provisioning(
        link, {"id": "tok_1", "apiKey": "sandbox-key-value"}
    )

    assert updated.selolibertaria_relay_status == "sent"
    assert updated.access_token_id == "tok_1"
    assert updated.key_version == 1
    assert updated.selolibertaria_relay_last_error is None
    sent_payload = fake_client.provisioned_calls[0]
    assert sent_payload["asaas_account_id"] == "acc_1"
    assert sent_payload["access_token_ref"] == "tok_1"
    assert sent_payload["api_key"] == "sandbox-key-value"


async def test_relay_initial_provisioning_blocked_when_asaas_payment_hides_api_key(db_session):
    """Documents the real gap found while implementing this slice:
    asaas_payment currently redacts `apiKey` from every access-token
    response (hide_api_key=True). Until that changes, this must never send
    a relay with a missing credential -- it marks the link `blocked` with a
    diagnostic instead.
    """
    link = await _make_link(db_session, asaas_account_id="acc_2")
    fake_client = _FakeRelayClient(_Result("sent", 202))
    service = SelolibertariaRelayService(fake_client, AsaasSubaccountLinkRepository(db_session))

    updated = await service.relay_initial_provisioning(link, {"id": "tok_2"})  # no apiKey

    assert updated.selolibertaria_relay_status == "blocked"
    assert updated.selolibertaria_relay_last_error is not None
    assert fake_client.provisioned_calls == []


async def test_relay_key_rotation_increments_key_version(db_session):
    link = await _make_link(db_session, asaas_account_id="acc_3")
    link.access_token_id = "tok_old"
    link.key_version = 1
    await db_session.commit()
    fake_client = _FakeRelayClient(_Result("acked", 200))
    service = SelolibertariaRelayService(fake_client, AsaasSubaccountLinkRepository(db_session))

    updated = await service.relay_key_rotation(
        link, {"id": "tok_new", "apiKey": "rotated-key"}, reason="proactive"
    )

    assert updated.selolibertaria_relay_status == "acked"
    assert updated.access_token_id == "tok_new"
    assert updated.key_version == 2
    account_id, sent_payload = fake_client.key_updated_calls[0]
    assert account_id == "acc_3"
    assert sent_payload["reason"] == "proactive"
    assert sent_payload["key_version"] == 2


# -- CredentialRotationService -----------------------------------------------


class _FakeAsaasClientForRotation:
    def __init__(self, *, tokens_response=None, create_response=None, create_error=None):
        self._tokens_response = tokens_response or {"data": []}
        self._create_response = create_response
        self._create_error = create_error
        self.create_access_token_calls: list[tuple[str, dict]] = []
        self.list_access_tokens_calls: list[str] = []

    async def list_access_tokens(self, account_id):
        self.list_access_tokens_calls.append(account_id)
        return self._tokens_response

    async def create_access_token(self, account_id, payload):
        self.create_access_token_calls.append((account_id, payload))
        if self._create_error is not None:
            raise self._create_error
        return self._create_response


async def test_proactive_sweep_rotates_token_expiring_soon(db_session):
    from datetime import UTC, datetime, timedelta

    link = await _make_link(db_session, asaas_account_id="acc_expiring")
    link.access_token_id = "tok_1"
    await db_session.commit()

    expires_soon = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    asaas_client = _FakeAsaasClientForRotation(
        tokens_response={"data": [{"id": "tok_1", "expirationDate": expires_soon}]},
        create_response={"id": "tok_2", "apiKey": "new-key"},
    )
    link_repository = AsaasSubaccountLinkRepository(db_session)
    relay_service = SelolibertariaRelayService(
        _FakeRelayClient(_Result("sent", 202)), link_repository
    )
    rotation_service = CredentialRotationService(asaas_client, link_repository, relay_service)

    touched = await rotation_service.run_proactive_sweep()

    assert len(touched) == 1
    assert touched[0].asaas_account_id == "acc_expiring"
    assert touched[0].access_token_id == "tok_2"
    assert asaas_client.create_access_token_calls[0][0] == "acc_expiring"


async def test_proactive_sweep_skips_token_far_from_expiring(db_session):
    from datetime import UTC, datetime, timedelta

    link = await _make_link(db_session, asaas_account_id="acc_fresh")
    link.access_token_id = "tok_1"
    await db_session.commit()

    far_future = (datetime.now(UTC) + timedelta(days=90)).isoformat()
    asaas_client = _FakeAsaasClientForRotation(
        tokens_response={"data": [{"id": "tok_1", "expirationDate": far_future}]},
    )
    link_repository = AsaasSubaccountLinkRepository(db_session)
    relay_service = SelolibertariaRelayService(
        _FakeRelayClient(_Result("sent", 202)), link_repository
    )
    rotation_service = CredentialRotationService(asaas_client, link_repository, relay_service)

    touched = await rotation_service.run_proactive_sweep()

    assert touched == []
    assert asaas_client.create_access_token_calls == []


async def test_rotation_blocked_when_asaas_window_is_closed(db_session):
    """00-levantamento-tecnico.md item D.1: managing a subaccount's access
    tokens from the root account requires a manual 2h window in the Asaas
    dashboard. A 403 (window closed) from asaas_payment must be an
    observable alert state, not an unhandled error.
    """
    link = await _make_link(db_session, asaas_account_id="acc_blocked")
    asaas_client = _FakeAsaasClientForRotation(
        create_error=UpstreamServiceError("asaas_payment returned 403 for POST ...")
    )
    link_repository = AsaasSubaccountLinkRepository(db_session)
    relay_client = _FakeRelayClient(_Result("sent", 202))
    relay_service = SelolibertariaRelayService(relay_client, link_repository)
    rotation_service = CredentialRotationService(asaas_client, link_repository, relay_service)

    updated = await rotation_service.rotate(link, reason="proactive")

    assert updated.selolibertaria_relay_status == "rotation_blocked"
    assert updated.selolibertaria_relay_last_error is not None
    assert relay_client.key_updated_calls == []


async def test_handle_account_status_event_triggers_rotation_for_known_triggers(db_session):
    link = await _make_link(db_session, asaas_account_id="acc_expiring_soon")
    link.access_token_id = "tok_1"
    await db_session.commit()

    asaas_client = _FakeAsaasClientForRotation(create_response={"id": "tok_2", "apiKey": "k"})
    link_repository = AsaasSubaccountLinkRepository(db_session)
    relay_service = SelolibertariaRelayService(
        _FakeRelayClient(_Result("sent", 202)), link_repository
    )
    rotation_service = CredentialRotationService(asaas_client, link_repository, relay_service)

    result = await rotation_service.handle_account_status_event(
        "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON", "acc_expiring_soon"
    )

    assert result is not None
    assert result.access_token_id == "tok_2"


async def test_handle_account_status_event_ignores_unrelated_events(db_session):
    await _make_link(db_session, asaas_account_id="acc_other")
    asaas_client = _FakeAsaasClientForRotation()
    link_repository = AsaasSubaccountLinkRepository(db_session)
    relay_service = SelolibertariaRelayService(
        _FakeRelayClient(_Result("sent", 202)), link_repository
    )
    rotation_service = CredentialRotationService(asaas_client, link_repository, relay_service)

    result = await rotation_service.handle_account_status_event(
        "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_APPROVED", "acc_other"
    )

    assert result is None
    assert asaas_client.create_access_token_calls == []


# -- AdminCompaniesService.link_subaccount (entregavel 2's actual trigger) --


class _FakeAsaasClientForProvisioning:
    """Provisioning a brand-new subaccount, then an initial access token
    that carries an apiKey -- i.e. the world once asaas_payment's
    hide_api_key gap (see relay_service.py) is closed on that route.
    """

    async def create_subaccount(self, payload):
        return {"id": "acc_provisioned", "walletId": "wallet_provisioned"}

    async def create_access_token(self, account_id, payload):
        assert account_id == "acc_provisioned"
        return {"id": "tok_provisioned", "apiKey": "brand-new-key"}


async def test_link_subaccount_relays_initial_credential_on_new_provisioning(db_session):
    from app.accounts.repository import AsaasSubaccountLinkRepository as _LinkRepo
    from app.admin.companies_service import AdminCompaniesService
    from app.admin.payments_service import AdminPaymentsService

    user = User(email="provisioning@example.com", password_hash="x", name="Dono")
    db_session.add(user)
    await db_session.flush()
    company = Company(user_id=user.id, name="Empresa Nova", cnpj="98765432000199")
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    link_repository = _LinkRepo(db_session)
    payments_service = AdminPaymentsService(_FakeAsaasClientForProvisioning(), link_repository)
    fake_relay_client = _FakeRelayClient(_Result("sent", 202))
    relay_service = SelolibertariaRelayService(fake_relay_client, link_repository)
    service = AdminCompaniesService(
        repository=None,  # unused by link_subaccount
        link_repository=link_repository,
        payments_service=payments_service,
        relay_service=relay_service,
    )

    link = await service.link_subaccount(company.id, {"name": "Empresa Nova"})

    assert link.asaas_account_id == "acc_provisioned"
    assert link.access_token_id == "tok_provisioned"
    assert link.selolibertaria_relay_status == "sent"
    assert len(fake_relay_client.provisioned_calls) == 1
    assert fake_relay_client.provisioned_calls[0]["api_key"] == "brand-new-key"


async def test_link_subaccount_does_not_relay_when_linking_an_existing_subaccount(db_session):
    from app.accounts.repository import AsaasSubaccountLinkRepository as _LinkRepo
    from app.admin.companies_service import AdminCompaniesService
    from app.admin.payments_service import AdminPaymentsService

    class _FakeAsaasClientExisting:
        async def get_subaccount(self, account_id):
            return {"id": account_id, "walletId": "wallet_existing"}

    user = User(email="existing@example.com", password_hash="x", name="Dono")
    db_session.add(user)
    await db_session.flush()
    company = Company(user_id=user.id, name="Empresa Existente", cnpj="11122233000144")
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    link_repository = _LinkRepo(db_session)
    payments_service = AdminPaymentsService(_FakeAsaasClientExisting(), link_repository)
    fake_relay_client = _FakeRelayClient(_Result("sent", 202))
    relay_service = SelolibertariaRelayService(fake_relay_client, link_repository)
    service = AdminCompaniesService(
        repository=None,
        link_repository=link_repository,
        payments_service=payments_service,
        relay_service=relay_service,
    )

    link = await service.link_subaccount(company.id, {"asaas_account_id": "acc_out_of_band"})

    assert link.asaas_account_id == "acc_out_of_band"
    assert link.selolibertaria_relay_status == "pending"  # default -- never touched
    assert fake_relay_client.provisioned_calls == []
