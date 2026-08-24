import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.repository import AccountRepository
from app.accounts.service import AccountService
from app.core.exceptions import UnauthorizedError
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
