import secrets

import pytest
from conftest import auth_header

pytestmark = pytest.mark.asyncio(scope="session")


async def test_register_rejects_short_password(client):
    resp = await client.post(
        "/v1/auth/register",
        json={
            "email": f"short-{secrets.token_hex(4)}@example.com",
            "password": "abc",
        },
    )
    assert resp.status_code == 422


async def test_existing_short_password_can_still_login(client, db):
    import auth as _auth

    email = f"legacy-{secrets.token_hex(4)}@example.com"
    password_hash = _auth.hash_password("abc")
    await db.execute(
        "INSERT INTO users (email, password_hash) VALUES (:email, :password_hash)",
        {"email": email, "password_hash": password_hash},
    )

    resp = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "abc"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


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
        "password": "valid-pass",
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
    resp = await client.post(
        "/v1/infer/doc-ocr",
        files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert resp.status_code == 401


async def test_rejects_invalid_key(client):
    resp = await client.post(
        "/v1/infer/doc-ocr",
        headers={"Authorization": "Bearer totally-fake-key"},
        files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert resp.status_code == 401


async def test_shared_lookup_rejects_revoked_key(db, regular_user):
    import auth as _auth

    raw_key = await _auth.create_api_key(db, regular_user["id"], rate_limit_per_min=60)
    await db.execute(
        "UPDATE api_keys SET revoked = TRUE WHERE key_hash = :key_hash",
        {"key_hash": _auth._hash_key(raw_key)},
    )

    record = await _auth.lookup_key(db, raw_key)
    assert record is None


async def test_rate_limit_enforced(client, db, regular_user):
    """A key with limit=2 should 429 on the third request."""
    import auth as _auth

    raw_key = await _auth.create_api_key(db, regular_user["id"], rate_limit_per_min=2)
    headers = auth_header(raw_key)

    # Exhaust the limit (requests may 404/503 on the worker, that's fine)
    for _ in range(2):
        await client.post(
            "/v1/infer/doc-ocr",
            headers=headers,
            files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
        )

    # Third call should be rate-limited
    resp = await client.post(
        "/v1/infer/doc-ocr",
        headers=headers,
        files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert resp.status_code == 429
