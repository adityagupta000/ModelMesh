import secrets

import pytest
from conftest import auth_header

pytestmark = pytest.mark.asyncio(scope="session")


async def test_register_user(client):
    resp = await client.post(
        "/v1/auth/register",
        json={
            "email": f"user-{secrets.token_hex(4)}@example.com",
            "password": "secret123",
        },
    )
    assert resp.status_code == 200


async def test_register_duplicate_email(client):
    payload = {
        "email": f"user-{secrets.token_hex(4)}@example.com",
        "password": "x",
    }
    await client.post("/v1/auth/register", json=payload)
    resp = await client.post("/v1/auth/register", json=payload)
    assert resp.status_code == 400


async def test_login_success(client, regular_user):
    resp = await client.post(
        "/v1/auth/login",
        json={"email": regular_user["email"], "password": "testpass"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_login_wrong_password(client, regular_user):
    resp = await client.post(
        "/v1/auth/login",
        json={"email": regular_user["email"], "password": "wrongpass"},
    )
    assert resp.status_code == 401


async def test_rejects_missing_key(client):
    resp = await client.post("/v1/infer/doc-ocr")
    assert resp.status_code == 401


async def test_rejects_invalid_key(client):
    resp = await client.post(
        "/v1/infer/doc-ocr",
        headers={"Authorization": "Bearer totally-fake-key"},
    )
    assert resp.status_code == 401


async def test_rate_limit_enforced(client, db, regular_user):
    """A key with limit=2 should 429 on the third request."""
    import auth as _auth

    raw_key = await _auth.create_api_key(db, regular_user["id"], rate_limit_per_min=2)
    headers = auth_header(raw_key)

    # Exhaust the limit (requests may 404/503 on the worker, that's fine)
    for _ in range(2):
        await client.post("/v1/infer/doc-ocr", headers=headers)

    # Third call should be rate-limited
    resp = await client.post("/v1/infer/doc-ocr", headers=headers)
    assert resp.status_code == 429
