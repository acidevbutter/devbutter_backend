import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import PasswordRecoveryToken, User
from app.accounts.repository import AccountRepository
from app.accounts.service import AccountService
from app.core.clients.relay_client import get_relay_client
from app.core.exceptions import UnauthorizedError
from app.main import app
from tests.conftest import auth_headers, signup


def _extraction(**overrides) -> dict:
    payload = {
        "problem_summary": "Precisa de um site institucional",
        "services_of_interest": ["website"],
        "budget_range": "R$5.000 - R$10.000",
        "timeline": "1 mes",
        "contact_name": "Visitante Teste",
        "contact_email": "visitante@example.com",
        "contact_phone": "+5511999999999",
        "company_name": "Empresa Visitante",
        "cnpj": "12345678000190",
    }
    payload.update(overrides)
    return payload


class _FakeRelayClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []

    def send(
        self, template_key: str, to: str, payload: dict, project_id=None, queue=None
    ) -> None:
        self.sent.append((template_key, to, payload))


def _mock_relay() -> _FakeRelayClient:
    fake = _FakeRelayClient()
    app.dependency_overrides[get_relay_client] = lambda: fake
    return fake


async def test_signup_returns_token_pair(client: AsyncClient):
    tokens = await signup(client)
    assert "access_token" in tokens
    assert "refresh_token" in tokens


async def test_signup_duplicate_email_returns_409(client: AsyncClient):
    await signup(client, email="dup@example.com")
    response = await client.post(
        "/accounts/signup",
        json={"email": "dup@example.com", "password": "supersecret", "name": "Another"},
    )
    assert response.status_code == 409


async def test_login_with_wrong_password_returns_401(client: AsyncClient):
    await signup(client, email="login@example.com")
    response = await client.post(
        "/accounts/login", json={"email": "login@example.com", "password": "wrong-password"}
    )
    assert response.status_code == 401


async def test_login_and_get_me(client: AsyncClient):
    await signup(client, email="me@example.com", name="Marcos")
    login_response = await client.post(
        "/accounts/login", json={"email": "me@example.com", "password": "supersecret"}
    )
    assert login_response.status_code == 200
    tokens = login_response.json()

    me_response = await client.get("/accounts/me", headers=auth_headers(tokens))
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "me@example.com"
    assert me_response.json()["user_id"]
    assert len(me_response.json()["accounts"]) == 1


async def test_me_without_token_returns_401(client: AsyncClient):
    response = await client.get("/accounts/me")
    assert response.status_code == 401


async def test_refresh_issues_new_token_pair(client: AsyncClient):
    tokens = await signup(client, email="refresh@example.com")
    response = await client.post(
        "/accounts/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200
    assert response.json()["access_token"] != tokens["access_token"]


async def test_password_recovery_is_generic_and_only_valid_user_is_queued(
    client: AsyncClient, db_session: AsyncSession
):
    fake_relay = _mock_relay()
    try:
        await signup(client, email="cliente@example.com")
        await signup(client, email="inativo@example.com")
        await signup(client, email="admin@example.com")
        users = list(
            (
                await db_session.execute(
                    select(User).where(User.email.in_(["inativo@example.com", "admin@example.com"]))
                )
            ).scalars()
        )
        for user in users:
            if user.email == "inativo@example.com":
                user.is_active = False
            else:
                user.is_admin = True
        await db_session.commit()

        responses = [
            await client.post("/accounts/password-recovery", json={"email": email})
            for email in [
                "CLIENTE@EXAMPLE.COM",
                "missing@example.com",
                "inativo@example.com",
                "admin@example.com",
            ]
        ]

        assert {response.status_code for response in responses} == {202}
        assert {response.text for response in responses} == {
            '{"message":"Se o e-mail estiver cadastrado, enviaremos instruções."}'
        }
        assert len(fake_relay.sent) == 1
        template_key, to, payload = fake_relay.sent[0]
        assert template_key == "devbutter_password_recovery"
        assert to == "cliente@example.com"
        assert payload["expires_at"]
        raw_token = payload["password_recovery_url"].rsplit("/", 1)[-1]

        tokens = list((await db_session.execute(select(PasswordRecoveryToken))).scalars())
        assert len(tokens) == 1
        assert tokens[0].token_hash != raw_token
        assert raw_token not in tokens[0].token_hash
        assert (await client.get(f"/accounts/password-setup/{raw_token}")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_relay_client, None)


async def test_password_recovery_invalidates_previous_token(
    client: AsyncClient, db_session: AsyncSession
):
    fake_relay = _mock_relay()
    try:
        await signup(client, email="repeat@example.com")

        first = await client.post(
            "/accounts/password-recovery", json={"email": "repeat@example.com"}
        )
        second = await client.post(
            "/accounts/password-recovery", json={"email": "REPEAT@EXAMPLE.COM"}
        )

        assert first.status_code == second.status_code == 202
        assert len(fake_relay.sent) == 2
        first_token = fake_relay.sent[0][2]["password_recovery_url"].rsplit("/", 1)[-1]
        second_token = fake_relay.sent[1][2]["password_recovery_url"].rsplit("/", 1)[-1]
        assert first_token != second_token
        result = await db_session.execute(select(PasswordRecoveryToken))
        tokens_by_hash = {token.token_hash: token for token in result.scalars()}
        assert tokens_by_hash[hashlib.sha256(first_token.encode()).hexdigest()].used_at is not None
        assert tokens_by_hash[hashlib.sha256(second_token.encode()).hexdigest()].used_at is None
        assert (await client.get(f"/accounts/password-setup/{first_token}")).status_code == 404
        assert (await client.get(f"/accounts/password-setup/{second_token}")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_relay_client, None)


async def test_password_recovery_check_and_completion_revoke_existing_sessions(
    client: AsyncClient,
):
    fake_relay = _mock_relay()
    try:
        old_tokens = await signup(client, email="recover@example.com")
        request = await client.post(
            "/accounts/password-recovery", json={"email": "recover@example.com"}
        )
        assert request.status_code == 202
        raw_token = fake_relay.sent[0][2]["password_recovery_url"].rsplit("/", 1)[-1]

        check = await client.get(f"/accounts/password-recovery/{raw_token}")
        assert check.status_code == 200
        assert check.json() == {"valid": True}

        complete = await client.post(
            "/accounts/password-recovery/complete",
            json={"token": raw_token, "password": "nova-senha-123"},
        )
        assert complete.status_code == 200, complete.text
        new_tokens = complete.json()
        assert new_tokens["access_token"]
        assert new_tokens["refresh_token"]

        assert (
            await client.get("/accounts/me", headers=auth_headers(old_tokens))
        ).status_code == 401
        assert (
            await client.post(
                "/accounts/refresh", json={"refresh_token": old_tokens["refresh_token"]}
            )
        ).status_code == 401
        assert (
            await client.get("/accounts/me", headers=auth_headers(new_tokens))
        ).status_code == 200

        used = await client.get(f"/accounts/password-recovery/{raw_token}")
        assert used.status_code == 404
        assert used.json() == {"detail": "Link inválido, expirado ou já utilizado"}
    finally:
        app.dependency_overrides.pop(get_relay_client, None)


async def test_password_recovery_rejects_invalid_expired_inactive_and_accountless_tokens(
    client: AsyncClient, db_session: AsyncSession
):
    fake_relay = _mock_relay()
    try:
        await signup(client, email="expire@example.com")
        await client.post("/accounts/password-recovery", json={"email": "expire@example.com"})
        expiring_token = fake_relay.sent[0][2]["password_recovery_url"].rsplit("/", 1)[-1]

        invalid_password = await client.post(
            "/accounts/password-recovery/complete",
            json={"token": expiring_token, "password": "short"},
        )
        assert invalid_password.status_code == 422
        assert (
            await client.get(f"/accounts/password-recovery/{expiring_token}")
        ).status_code == 200

        recovery_token = (
            await db_session.execute(select(PasswordRecoveryToken))
        ).scalar_one()
        recovery_token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.commit()
        expired = await client.post(
            "/accounts/password-recovery/complete",
            json={"token": expiring_token, "password": "nova-senha-123"},
        )
        assert expired.status_code == 404
        assert expired.json() == {"detail": "Link inválido, expirado ou já utilizado"}

        await signup(client, email="inactive-recovery@example.com")
        await signup(client, email="accountless-recovery@example.com")
        users = {
            user.email: user
            for user in (
                await db_session.execute(
                    select(User).where(
                        User.email.in_(
                            ["inactive-recovery@example.com", "accountless-recovery@example.com"]
                        )
                    )
                )
            ).scalars()
        }
        users["inactive-recovery@example.com"].is_active = False
        accountless_user = users["accountless-recovery@example.com"]
        accounts = await AccountRepository(db_session).list_for_user(accountless_user.id)
        await db_session.delete(accounts[0])
        await db_session.commit()

        repository = AccountRepository(db_session)
        inactive_token, _ = await repository.create_password_recovery_token(
            users["inactive-recovery@example.com"].id
        )
        accountless_token, _ = await repository.create_password_recovery_token(accountless_user.id)
        for raw_token in (inactive_token, accountless_token, "not-a-token"):
            response = await client.post(
                "/accounts/password-recovery/complete",
                json={"token": raw_token, "password": "nova-senha-123"},
            )
            assert response.status_code == 404
            assert response.json() == {"detail": "Link inválido, expirado ou já utilizado"}
    finally:
        app.dependency_overrides.pop(get_relay_client, None)


async def test_password_recovery_completion_has_exactly_one_concurrent_winner(client: AsyncClient):
    fake_relay = _mock_relay()
    try:
        await signup(client, email="concurrent-recovery@example.com")
        await client.post(
            "/accounts/password-recovery", json={"email": "concurrent-recovery@example.com"}
        )
        raw_token = fake_relay.sent[0][2]["password_recovery_url"].rsplit("/", 1)[-1]

        responses = await asyncio.gather(
            client.post(
                "/accounts/password-recovery/complete",
                json={"token": raw_token, "password": "nova-senha-123"},
            ),
            client.post(
                "/accounts/password-recovery/complete",
                json={"token": raw_token, "password": "nova-senha-456"},
            ),
        )

        assert sorted(response.status_code for response in responses) == [200, 404]
        loser = next(response for response in responses if response.status_code == 404)
        assert loser.json() == {"detail": "Link inválido, expirado ou já utilizado"}
    finally:
        app.dependency_overrides.pop(get_relay_client, None)


async def test_user_can_select_another_owned_account_and_data_stays_scoped(client: AsyncClient):
    tokens = await signup(client, email="multi-account@example.com", product_name="Produto A")
    headers = auth_headers(tokens)

    second_account = await client.post(
        "/accounts",
        json={"product_name": "Produto B"},
        headers=headers,
    )
    assert second_account.status_code == 201

    accounts_response = await client.get("/accounts", headers=headers)
    assert accounts_response.status_code == 200
    assert [item["product_name"] for item in accounts_response.json()] == ["Produto A", "Produto B"]

    first_catalog = await client.post(
        "/service-catalog",
        json={"type": "services", "description": "Somente produto A"},
        headers=headers,
    )
    assert first_catalog.status_code == 201

    select_response = await client.post(
        "/accounts/select",
        json={"account_id": second_account.json()["id"]},
        headers=headers,
    )
    assert select_response.status_code == 200
    second_headers = auth_headers(select_response.json())

    selected_me = await client.get("/accounts/me", headers=second_headers)
    assert selected_me.status_code == 200
    assert selected_me.json()["product_name"] == "Produto B"

    second_catalog = await client.get("/service-catalog", headers=second_headers)
    assert second_catalog.status_code == 200
    assert second_catalog.json() == []

    company_response = await client.post(
        "/accounts/companies",
        json={"name": "Empresa C", "cnpj": "12345678000190"},
        headers=second_headers,
    )
    assert company_response.status_code == 201

    third_account = await client.post(
        "/accounts",
        json={"product_name": "Produto C", "company_id": company_response.json()["id"]},
        headers=second_headers,
    )
    assert third_account.status_code == 201
    assert third_account.json()["company_name"] == "Empresa C"

    third_tokens = await client.post(
        "/accounts/select",
        json={"account_id": third_account.json()["id"]},
        headers=second_headers,
    )
    third_me = await client.get("/accounts/me", headers=auth_headers(third_tokens.json()))
    assert third_me.status_code == 200
    assert third_me.json()["company_name"] == "Empresa C"
    assert third_me.json()["cnpj"] == "12345678000190"


async def test_get_or_create_from_extraction_creates_account_for_new_email(
    db_session: AsyncSession,
):
    service = AccountService(AccountRepository(db_session))

    result = await service.get_or_create_from_extraction(_extraction())

    assert result.created is True
    assert result.tokens is not None
    assert result.user.email == "visitante@example.com"
    assert result.user.auth_method == "magic_link"
    assert result.user.password_hash is None
    assert result.company.name == "Empresa Visitante"
    assert result.company.cnpj == "12345678000190"
    assert result.account.product_name == "Diagnóstico IA"


async def test_login_rejects_magic_link_account_without_password(db_session: AsyncSession):
    service = AccountService(AccountRepository(db_session))
    await service.get_or_create_from_extraction(_extraction())

    with pytest.raises(UnauthorizedError):
        await service.login("visitante@example.com", "any-password")


async def test_get_or_create_from_extraction_does_not_auto_login_existing_email(
    client: AsyncClient, db_session: AsyncSession
):
    await signup(client, email="already-a-client@example.com", name="Cliente Existente")
    service = AccountService(AccountRepository(db_session))

    result = await service.get_or_create_from_extraction(
        _extraction(contact_email="already-a-client@example.com")
    )

    assert result.created is False
    assert result.tokens is None
    assert result.user.email == "already-a-client@example.com"
    assert result.user.name == "Cliente Existente"
