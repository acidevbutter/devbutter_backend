from httpx import AsyncClient

from tests.conftest import auth_headers, signup


async def test_create_and_list_catalog_entry(client: AsyncClient):
    tokens = await signup(client, email="catalog@example.com")
    headers = auth_headers(tokens)

    create_response = await client.post(
        "/service-catalog",
        json={"type": "ecommerce", "description": "Loja de roupas"},
        headers=headers,
    )
    assert create_response.status_code == 201
    entry_id = create_response.json()["id"]

    list_response = await client.get("/service-catalog", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert list_response.json()[0]["id"] == entry_id


async def test_cannot_update_another_accounts_entry(client: AsyncClient):
    owner_tokens = await signup(client, email="owner@example.com")
    create_response = await client.post(
        "/service-catalog",
        json={"type": "ecommerce", "description": "Loja"},
        headers=auth_headers(owner_tokens),
    )
    entry_id = create_response.json()["id"]

    other_tokens = await signup(client, email="other@example.com")
    response = await client.patch(
        f"/service-catalog/{entry_id}",
        json={"description": "hijacked"},
        headers=auth_headers(other_tokens),
    )
    assert response.status_code == 404


async def test_delete_catalog_entry(client: AsyncClient):
    tokens = await signup(client, email="delete@example.com")
    headers = auth_headers(tokens)
    create_response = await client.post(
        "/service-catalog", json={"type": "services", "description": "Consultoria"}, headers=headers
    )
    entry_id = create_response.json()["id"]

    delete_response = await client.delete(f"/service-catalog/{entry_id}", headers=headers)
    assert delete_response.status_code == 204

    list_response = await client.get("/service-catalog", headers=headers)
    assert list_response.json() == []
