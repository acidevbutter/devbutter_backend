from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, Company, CompanyBillingProfile, User
from app.accounts.repository import (
    AccountRepository,
    AsaasSubaccountLinkRepository,
    CompanyBillingProfileRepository,
)
from app.core.exceptions import UpstreamServiceError, ValidationFailedError
from app.settings.config import settings
from app.subaccounts_provisioning.service import (
    ACCOUNT_STATUS_EVENTS,
    SubaccountProvisioningService,
)


class _FakeAsaas:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.created_bodies: list[dict[str, Any]] = []

    async def create_subaccount(self, body: dict[str, Any]) -> dict[str, Any]:
        self.created_bodies.append(body)
        if self.fail:
            raise UpstreamServiceError("asaas_payment returned 400")
        return {"id": "acc_123", "walletId": "wal_123"}


class _FakeNotifications:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify_subaccount_onboarding(self, user: User, company_name: str) -> None:
        self.calls.append((user.email, company_name))


async def _seed(db: AsyncSession, *, with_profile: bool = True) -> Company:
    user = User(email=f"{uuid.uuid4()}@ex.com", password_hash="x", name="Dona")
    db.add(user)
    await db.flush()
    company = Company(user_id=user.id, name="Loja PJ", cnpj="11222333000181")
    db.add(company)
    await db.flush()
    db.add(Account(company_id=company.id, product_name="Prod"))
    if with_profile:
        db.add(
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
    await db.commit()
    return company


def _service(
    db: AsyncSession, fake: _FakeAsaas, notes: _FakeNotifications
) -> SubaccountProvisioningService:
    return SubaccountProvisioningService(
        fake,  # type: ignore[arg-type]
        AsaasSubaccountLinkRepository(db),
        CompanyBillingProfileRepository(db),
        AccountRepository(db),
        notes,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _backend_url(monkeypatch):
    monkeypatch.setattr(settings, "backend_public_base_url", "https://api.devbutter.test")
    monkeypatch.setattr(settings, "asaas_webhook_access_token", "whk")


async def test_provision_creates_link_with_webhook(db_session: AsyncSession) -> None:
    company = await _seed(db_session)
    fake, notes = _FakeAsaas(), _FakeNotifications()
    result = await _service(db_session, fake, notes).provision(company.id, actor="admin:1")

    assert result.created is True
    assert result.link.asaas_account_id == "acc_123"
    assert result.link.wallet_id == "wal_123"
    assert result.link.provisioning_status == "PROVISIONED"
    assert result.link.onboarding_status == "AWAITING_ONBOARDING"
    body = fake.created_bodies[0]
    assert body["cpfCnpj"] == "11222333000181"
    assert body["companyType"] == "LIMITED"
    hook = body["webhooks"][0]
    assert hook["url"] == "https://api.devbutter.test/webhooks/asaas"
    assert hook["authToken"] == "whk"
    assert set(ACCOUNT_STATUS_EVENTS).issubset(set(hook["events"]))
    assert len(notes.calls) == 1 and notes.calls[0][1] == "Loja PJ"
    # Neither was set on the seeded profile -- must be left out entirely,
    # not sent as null/"". Asaas rejects an empty `site` (fails its URL
    # check on a blank string, unlike a field that's simply absent).
    assert "site" not in body
    assert "complement" not in body


async def test_provision_includes_optional_fields_only_when_set(db_session: AsyncSession) -> None:
    company = await _seed(db_session)
    profile_repo = CompanyBillingProfileRepository(db_session)
    profile = await profile_repo.get_by_company(company.id)
    assert profile is not None
    profile.site = "https://loja.com.br"
    profile.complement = "sala 2"
    await db_session.commit()

    fake, notes = _FakeAsaas(), _FakeNotifications()
    await _service(db_session, fake, notes).provision(company.id, actor="admin:1")

    body = fake.created_bodies[0]
    assert body["site"] == "https://loja.com.br"
    assert body["complement"] == "sala 2"


async def test_provision_is_idempotent(db_session: AsyncSession) -> None:
    company = await _seed(db_session)
    fake, notes = _FakeAsaas(), _FakeNotifications()
    svc = _service(db_session, fake, notes)
    await svc.provision(company.id, actor="webhook:first_payment")
    second = await svc.provision(company.id, actor="webhook:first_payment")
    assert second.created is False
    assert len(fake.created_bodies) == 1


async def test_provision_missing_profile_raises(db_session: AsyncSession) -> None:
    company = await _seed(db_session, with_profile=False)
    with pytest.raises(ValidationFailedError):
        await _service(db_session, _FakeAsaas(), _FakeNotifications()).provision(
            company.id, actor="admin:1"
        )


async def test_provision_failure_marks_creation_failed(db_session: AsyncSession) -> None:
    company = await _seed(db_session)
    fake, notes = _FakeAsaas(fail=True), _FakeNotifications()
    result = await _service(db_session, fake, notes).provision(company.id, actor="admin:1")

    assert result.created is False
    assert result.link.provisioning_status == "CREATION_FAILED"
    assert result.link.last_error
    assert notes.calls == []


async def test_create_manual_uses_admin_payload_and_attaches_webhook(
    db_session: AsyncSession,
) -> None:
    company = await _seed(db_session, with_profile=False)
    fake, notes = _FakeAsaas(), _FakeNotifications()
    payload = {
        "name": "Loja PJ Manual LTDA",
        "email": "manual@loja.com",
        "cpfCnpj": "11222333000181",
        "companyType": "LIMITED",
        "mobilePhone": "11999997777",
        "incomeValue": 4000.0,
        "address": "Rua B",
        "addressNumber": "20",
        "province": "Centro",
        "postalCode": "01001000",
    }

    result = await _service(db_session, fake, notes).create_manual(
        company.id, payload, actor="admin-manual:1"
    )

    assert result.created is True
    assert result.link.asaas_account_id == "acc_123"
    assert result.link.provisioning_status == "PROVISIONED"
    body = fake.created_bodies[0]
    assert body["email"] == "manual@loja.com"
    hook = body["webhooks"][0]
    assert hook["url"] == "https://api.devbutter.test/webhooks/asaas"
    assert set(ACCOUNT_STATUS_EVENTS).issubset(set(hook["events"]))
    assert len(notes.calls) == 1


async def test_create_manual_is_idempotent_once_provisioned(db_session: AsyncSession) -> None:
    company = await _seed(db_session, with_profile=False)
    fake, notes = _FakeAsaas(), _FakeNotifications()
    svc = _service(db_session, fake, notes)
    payload = {"name": "x", "email": "x@x.com"}

    await svc.create_manual(company.id, payload, actor="admin-manual:1")
    second = await svc.create_manual(company.id, payload, actor="admin-manual:1")

    assert second.created is False
    assert second.detail == "already_provisioned"
    assert len(fake.created_bodies) == 1


async def test_create_manual_failure_marks_creation_failed_for_retry(
    db_session: AsyncSession,
) -> None:
    company = await _seed(db_session, with_profile=False)
    fake, notes = _FakeAsaas(fail=True), _FakeNotifications()

    result = await _service(db_session, fake, notes).create_manual(
        company.id, {"name": "x", "email": "x@x.com"}, actor="admin-manual:1"
    )

    assert result.created is False
    assert result.link.provisioning_status == "CREATION_FAILED"
    assert result.link.last_error
    assert notes.calls == []
