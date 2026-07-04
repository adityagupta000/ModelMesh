"""
Test fixtures.

Tests run against a real Postgres + Redis (via docker-compose or a local instance).
Set TEST_DATABASE_URL and TEST_REDIS_URL env vars, or rely on the defaults below.
"""

import asyncio
import hashlib
import os
import secrets

import pytest
import pytest_asyncio
from databases import Database
from httpx import AsyncClient, ASGITransport
import redis.asyncio as aioredis

# ── point at the test DB ──────────────────────────────────────────────────────
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql://user:pass@localhost/modelmesh_test"
)
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/1")

# Patch module-level singletons before importing the app
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["JWT_SECRET"] = "test-secret"

# ── import app after env is patched ──────────────────────────────────────────
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gateway"))

from main import app  # noqa: E402  (must come after env patch)
import db as _db      # noqa: E402
import auth as _auth  # noqa: E402
import registry       # noqa: E402


# ── event loop ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── DB + redis fixtures ───────────────────────────────────────────────────────
@pytest_asyncio.fixture(scope="session")
async def db():
    database = Database(TEST_DATABASE_URL)
    await database.connect()
    _db.database = database
    yield database
    await database.disconnect()


@pytest_asyncio.fixture(scope="session")
async def redis_client():
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    _db.redis = r
    yield r
    await r.aclose()


# ── HTTP client ───────────────────────────────────────────────────────────────
@pytest_asyncio.fixture(scope="session")
async def client(db, redis_client):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ── seed a regular user + API key ─────────────────────────────────────────────
@pytest_asyncio.fixture(scope="session")
async def regular_user(db):
    email = f"test_{secrets.token_hex(4)}@example.com"
    pw_hash = _auth.hash_password("testpass")
    row = await db.fetch_one(
        "INSERT INTO users (email, password_hash) VALUES (:e, :p) RETURNING id",
        {"e": email, "p": pw_hash},
    )
    return {"id": str(row["id"]), "email": email}


@pytest_asyncio.fixture(scope="session")
async def regular_key(db, regular_user) -> str:
    raw = await _auth.create_api_key(db, regular_user["id"], rate_limit_per_min=60, is_admin=False)
    return raw


@pytest_asyncio.fixture(scope="session")
async def admin_user(db):
    email = f"admin_{secrets.token_hex(4)}@example.com"
    pw_hash = _auth.hash_password("adminpass")
    row = await db.fetch_one(
        "INSERT INTO users (email, password_hash) VALUES (:e, :p) RETURNING id",
        {"e": email, "p": pw_hash},
    )
    return {"id": str(row["id"]), "email": email}


@pytest_asyncio.fixture(scope="session")
async def admin_key(db, admin_user) -> str:
    raw = await _auth.create_api_key(db, admin_user["id"], rate_limit_per_min=1000, is_admin=True)
    return raw


# ── fetch the seeded doc-ocr model id ────────────────────────────────────────
@pytest_asyncio.fixture(scope="session")
async def doc_ocr_model_id(db) -> str:
    row = await db.fetch_one(
        "SELECT id FROM models WHERE name = 'doc-ocr' AND version = 'v1'"
    )
    assert row, "doc-ocr model not seeded — run db/init.sql"
    return str(row["id"])


def auth_header(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}
