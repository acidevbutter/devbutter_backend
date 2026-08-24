from __future__ import annotations

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import AsaasSubaccountLink, Company, User
from app.core.clients.asaas_client import get_asaas_client
from app.main import app
from tests.conftest import auth_headers, signup


class FakeAsaasClient:
    """Records every call and returns canned data -- AdminPaymentsService's
    job is orchestration/quota-checking, not talking to asaas_payment
    itself, so these tests never need a real AsaasClient.
    """

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.split_rules: list[dict] = []
        self.access_tokens_created: list[dict] = []

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))

    async def list_subaccounts(self, filters=None):
        self._record("list_subaccounts", (filters,), {})
        return {"data": [{"id": "acc_1"}], "totalCount": 1}

    async def get_subaccount(self, account_id):
        self._record("get_subaccount", (account_id,), {})
        return {"id": account_id, "walletId": "wallet_1"}

    async def create_subaccount(self, payload):
        self._record("create_subaccount", (payload,), {})
        return {"id": "acc_new", "walletId": "wallet_new"}

    async def close_subaccount(self, account_id):
        self._record("close_subaccount", (account_id,), {})
        return {"id": account_id, "deleted": True}

    async def resend_activation_link(self, account_id):
        self._record("resend_activation_link", (account_id,), {})
        return {}

    async def list_access_tokens(self, account_id):
        self._record("list_access_tokens", (account_id,), {})
        return {"data": []}

    async def create_access_token(self, account_id, payload):
        self._record("create_access_token", (account_id, payload), {})
        token = {"id": f"tok_{len(self.access_tokens_created) + 1}", "name": payload["name"]}
        self.access_tokens_created.append(token)
        return token

    async def revoke_access_token(self, account_id, token_id):
        self._record("revoke_access_token", (account_id, token_id), {})
        return {}

    async def list_split_rules(self, account_id):
        self._record("list_split_rules", (account_id,), {})
        return self.split_rules

    async def create_split_rule(self, account_id, payload):
        self._record("create_split_rule", (account_id, payload), {})
        return {"id": "rule_new", **payload, "active": True}

    async def update_split_rule(self, account_id, rule_id, payload):
        self._record("update_split_rule", (account_id, rule_id, payload), {})
        return {"id": rule_id, **payload}

    async def list_payments(self, account_id, filters=None):
        self._record("list_payments", (account_id, filters), {})
        return {"data": []}

    async def get_payment(self, account_id, payment_id):
        self._record("get_payment", (account_id, payment_id), {})
        return {"id": payment_id}

    async def refund_payment(self, account_id, payment_id, payload=None):
        self._record("refund_payment", (account_id, payment_id, payload), {})
        return {"id": payment_id, "status": "REFUNDED"}

    async def cancel_payment(self, account_id, payment_id):
        self._record("cancel_payment", (account_id, payment_id), {})
        return {"id": payment_id, "deleted": True}

    async def get_audit_logs(self, filters=None):
        self._record("get_audit_logs", (filters,), {})
        return [{"id": "log_1", "client_id": "store-a"}]


async def _make_admin(db_session: AsyncSession, user: User) -> None:
    user.is_admin = True
    await db_session.commit()


async def _signup_admin(client: AsyncClient, db_session: AsyncSession, email: str) -> dict:
    tokens = await signup(client, email=email)
    me = await client.get("/accounts/me", headers=auth_headers(tokens))
    user = await db_session.get(User, UUID(me.json()["user_id"]))
    assert user is not None
    await _make_admin(db_session, user)
    return tokens


async def _create_company(db_session: AsyncSession, name: str = "Empresa Teste") -> Company:
    tokens_user = User(
        email=f"{name.lower().replace(' ', '')}@example.com",
        password_hash="x",
        name="Dono",
    )
    db_session.add(tokens_user)
    await db_session.flush()
    company = Company(user_id=tokens_user.id, name=name, cnpj="12345678000199")
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    return company


async def test_admin_subaccount_routes_require_admin(client: AsyncClient):
    tokens = await signup(client, email="not-admin@example.com")

    response = await client.get("/admin/subaccounts", headers=auth_headers(tokens))

    assert response.status_code == 403


async def test_admin_can_list_and_create_subaccounts(client: AsyncClient, db_session: AsyncSession):
    admin_tokens = await _signup_admin(client, db_session, "admin-sub@example.com")
    fake = FakeAsaasClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake

    listing = await client.get("/admin/subaccounts", headers=auth_headers(admin_tokens))
    assert listing.status_code == 200
    assert listing.json()["totalCount"] == 1

    created = await client.post(
        "/admin/subaccounts", json={"name": "Loja"}, headers=auth_headers(admin_tokens)
    )
    assert created.status_code == 200
    assert created.json()["id"] == "acc_new"

    app.dependency_overrides.pop(get_asaas_client, None)


async def test_admin_create_split_rule_without_quota_passes_through(
    client: AsyncClient, db_session: AsyncSession
):
    admin_tokens = await _signup_admin(client, db_session, "admin-split1@example.com")
    fake = FakeAsaasClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake

    response = await client.post(
        "/admin/subaccounts/acc_1/split-rules",
        json={"walletId": "wallet_x", "type": "PERCENTAGE", "value": "50.00"},
        headers=auth_headers(admin_tokens),
    )

    assert response.status_code == 200
    assert response.json()["id"] == "rule_new"

    app.dependency_overrides.pop(get_asaas_client, None)


async def test_admin_create_split_rule_rejects_when_over_company_quota(
    client: AsyncClient, db_session: AsyncSession
):
    admin_tokens = await _signup_admin(client, db_session, "admin-split2@example.com")
    company = await _create_company(db_session, "Empresa Cota")
    db_session.add(
        AsaasSubaccountLink(
            company_id=company.id,
            asaas_account_id="acc_quota",
            wallet_id="wallet_1",
            split_quota_percent="20.00",
        )
    )
    await db_session.commit()

    fake = FakeAsaasClient()
    fake.split_rules = [
        {
            "id": "rule_existing",
            "walletId": "w1",
            "type": "PERCENTAGE",
            "value": "15.00",
            "active": True,
        }
    ]
    app.dependency_overrides[get_asaas_client] = lambda: fake

    response = await client.post(
        "/admin/subaccounts/acc_quota/split-rules",
        json={"walletId": "wallet_new", "type": "PERCENTAGE", "value": "10.00"},
        headers=auth_headers(admin_tokens),
    )

    assert response.status_code == 422
    assert "cota" in response.json()["detail"]

    within_quota = await client.post(
        "/admin/subaccounts/acc_quota/split-rules",
        json={"walletId": "wallet_ok", "type": "PERCENTAGE", "value": "5.00"},
        headers=auth_headers(admin_tokens),
    )
    assert within_quota.status_code == 200

    app.dependency_overrides.pop(get_asaas_client, None)


async def test_admin_companies_list_and_detail(client: AsyncClient, db_session: AsyncSession):
    admin_tokens = await _signup_admin(client, db_session, "admin-companies@example.com")
    company = await _create_company(db_session, "Empresa Sem Meio")
    linked_company = await _create_company(db_session, "Empresa Com Meio")
    db_session.add(
        AsaasSubaccountLink(
            company_id=linked_company.id,
            asaas_account_id="acc_linked",
            wallet_id="wallet_linked",
            split_quota_percent="30.00",
        )
    )
    await db_session.commit()

    fake = FakeAsaasClient()
    fake.split_rules = [
        {"id": "r1", "walletId": "w1", "type": "PERCENTAGE", "value": "10.00", "active": True}
    ]
    app.dependency_overrides[get_asaas_client] = lambda: fake

    listing = await client.get("/admin/companies", headers=auth_headers(admin_tokens))
    assert listing.status_code == 200
    names = {item["name"]: item for item in listing.json()["items"]}
    assert names["Empresa Sem Meio"]["has_payment_method"] is False
    assert names["Empresa Com Meio"]["has_payment_method"] is True
    assert names["Empresa Com Meio"]["used_percent"] == "10.00"

    detail = await client.get(
        f"/admin/companies/{linked_company.id}", headers=auth_headers(admin_tokens)
    )
    assert detail.status_code == 200
    assert detail.json()["link"]["asaas_account_id"] == "acc_linked"
    assert len(detail.json()["split_rules"]) == 1

    no_link_detail = await client.get(
        f"/admin/companies/{company.id}", headers=auth_headers(admin_tokens)
    )
    assert no_link_detail.status_code == 200
    assert no_link_detail.json()["link"] is None

    app.dependency_overrides.pop(get_asaas_client, None)


async def test_admin_set_split_quota_requires_existing_link(
    client: AsyncClient, db_session: AsyncSession
):
    admin_tokens = await _signup_admin(client, db_session, "admin-quota@example.com")
    company = await _create_company(db_session, "Empresa Sem Link")
    app.dependency_overrides[get_asaas_client] = lambda: FakeAsaasClient()

    response = await client.patch(
        f"/admin/companies/{company.id}/split-quota",
        json={"split_quota_percent": "25.00"},
        headers=auth_headers(admin_tokens),
    )

    assert response.status_code == 404

    app.dependency_overrides.pop(get_asaas_client, None)


async def test_admin_link_subaccount_creates_new_and_rejects_duplicate(
    client: AsyncClient, db_session: AsyncSession
):
    admin_tokens = await _signup_admin(client, db_session, "admin-link@example.com")
    company = await _create_company(db_session, "Empresa Para Vincular")
    fake = FakeAsaasClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake

    response = await client.post(
        f"/admin/companies/{company.id}/link-subaccount",
        json={"name": "Loja Nova"},
        headers=auth_headers(admin_tokens),
    )
    assert response.status_code == 200
    assert response.json()["asaas_account_id"] == "acc_new"

    duplicate = await client.post(
        f"/admin/companies/{company.id}/link-subaccount",
        json={"name": "Loja Nova 2"},
        headers=auth_headers(admin_tokens),
    )
    assert duplicate.status_code == 422

    app.dependency_overrides.pop(get_asaas_client, None)


async def test_feature_enable_requires_payment_method_then_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    admin_tokens = await _signup_admin(client, db_session, "admin-feature@example.com")
    company = await _create_company(db_session, "Empresa Feature")
    fake = FakeAsaasClient()
    app.dependency_overrides[get_asaas_client] = lambda: fake

    blocked = await client.post(
        f"/admin/companies/{company.id}/features/sells_via_api/enable",
        headers=auth_headers(admin_tokens),
    )
    assert blocked.status_code == 422

    db_session.add(
        AsaasSubaccountLink(company_id=company.id, asaas_account_id="acc_feat", wallet_id="w1")
    )
    await db_session.commit()

    enabled = await client.post(
        f"/admin/companies/{company.id}/features/sells_via_api/enable",
        headers=auth_headers(admin_tokens),
    )
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"
    assert enabled.json()["asaas_access_token_id"] == "tok_1"

    idempotent = await client.post(
        f"/admin/companies/{company.id}/features/sells_via_api/enable",
        headers=auth_headers(admin_tokens),
    )
    assert idempotent.status_code == 200
    assert idempotent.json()["asaas_access_token_id"] == "tok_1"
    assert len(fake.access_tokens_created) == 1

    disabled = await client.post(
        f"/admin/companies/{company.id}/features/sells_via_api/disable",
        headers=auth_headers(admin_tokens),
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert ("revoke_access_token", ("acc_feat", "tok_1"), {}) in fake.calls

    listing = await client.get(
        f"/admin/companies/{company.id}/features", headers=auth_headers(admin_tokens)
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    app.dependency_overrides.pop(get_asaas_client, None)
