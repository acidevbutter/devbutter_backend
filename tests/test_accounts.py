from httpx import AsyncClient

from tests.conftest import auth_headers, signup


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
