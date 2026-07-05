import hashlib
import json
import time

import httpx
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from db import database, redis, connect, disconnect
from auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_api_key,
    make_verify_api_key,
    make_verify_admin_key,
)
from rate_limit import check_rate_limit
import registry
from schemas import (
    UserCreate,
    TokenResponse,
    ApiKeyCreate,
    ApiKeyResponse,
    ModelRegistration,
    ModelUpdate,
)

app = FastAPI(title="ModelMesh Gateway", version="1.0.0")

# ── auth dependencies resolved at startup ────────────────────────────────────
verify_api_key = None
verify_admin_key = None


@app.on_event("startup")
async def startup():
    global verify_api_key, verify_admin_key
    await connect()
    verify_api_key = make_verify_api_key(database)
    verify_admin_key = make_verify_admin_key(database)


@app.on_event("shutdown")
async def shutdown():
    await disconnect()


# ── helpers ──────────────────────────────────────────────────────────────────

def _key_dep():
    """Thin wrapper so FastAPI resolves verify_api_key after startup."""
    async def inner(authorization: str | None = None):
        return await verify_api_key(authorization)
    return Depends(inner)


def _admin_dep():
    async def inner(authorization: str | None = None):
        return await verify_admin_key(authorization)
    return Depends(inner)


async def _log_request(key_id: str, model_id: str, model_name: str, status: str, latency_ms: int):
    await database.execute(
        """INSERT INTO requests (api_key_id, model_id, model_type, status, latency_ms)
           VALUES (:key_id, :model_id, :model_name, :status, :latency_ms)""",
        {
            "key_id": key_id,
            "model_id": model_id,
            "model_name": model_name,
            "status": status,
            "latency_ms": latency_ms,
        },
    )


async def route_request(endpoint: str, protocol: str, payload: bytes, filename: str = "file") -> dict:
    if protocol == "http":
        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"file": (filename, payload)}
            resp = await client.post(endpoint, files=files)
            resp.raise_for_status()
            return resp.json()
    elif protocol == "grpc":
        raise NotImplementedError("gRPC routing arrives in Phase 3")
    else:
        raise ValueError(f"Unsupported protocol: {protocol}")


# ── auth endpoints ────────────────────────────────────────────────────────────

@app.post("/v1/auth/register", tags=["auth"])
async def register(payload: UserCreate):
    existing = await database.fetch_one(
        "SELECT id FROM users WHERE email = :email", {"email": payload.email}
    )
    if existing:
        raise HTTPException(400, "Email already registered")
    password_hash = hash_password(payload.password)
    await database.execute(
        "INSERT INTO users (email, password_hash) VALUES (:email, :pw)",
        {"email": payload.email, "pw": password_hash},
    )
    return {"message": "User registered"}


@app.post("/v1/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(payload: UserCreate):
    row = await database.fetch_one(
        "SELECT id, password_hash FROM users WHERE email = :email", {"email": payload.email}
    )
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token(str(row["id"]))
    return {"access_token": token}


@app.post("/v1/auth/api-keys", response_model=ApiKeyResponse, tags=["auth"])
async def issue_api_key(payload: ApiKeyCreate, key=_key_dep()):
    raw_key = await create_api_key(database, key.user_id, payload.rate_limit_per_min)
    return {"key": raw_key, "rate_limit_per_min": payload.rate_limit_per_min}


# ── inference endpoint ────────────────────────────────────────────────────────

@app.post("/v1/infer/{model_name}", tags=["inference"])
async def infer(
    model_name: str,
    file: UploadFile = File(...),
    version: str = "v1",
    key=_key_dep(),
):
    start = time.perf_counter()
    await check_rate_limit(key.id, key.rate_limit_per_min, redis)

    model = await registry.get_model(database, model_name, version)
    if not model:
        raise HTTPException(404, "Unknown model")
    if model.status != "active":
        raise HTTPException(503, "Model is currently disabled")

    payload_bytes = await file.read()
    filename = file.filename or "file"

    # Redis cache keyed by model + input hash
    cache_key = f"cache:{model.id}:{hashlib.sha256(payload_bytes).hexdigest()}"
    cached = await redis.get(cache_key)
    cached_hit = cached is not None
    if cached_hit:
        result = json.loads(cached)
    else:
        result = await route_request(model.endpoint, model.protocol, payload_bytes, filename)
        await redis.set(cache_key, json.dumps(result), ex=3600)

    latency_ms = int((time.perf_counter() - start) * 1000)
    await _log_request(key.id, model.id, model_name, "success", latency_ms)

    return {
        "result": result,
        "model": model.name,
        "version": model.version,
        "latency_ms": latency_ms,
        "cached": cached_hit,
    }


# ── model registry endpoints ──────────────────────────────────────────────────

@app.get("/v1/models", tags=["registry"])
async def list_models_endpoint():
    models = await registry.list_models(database)
    return [vars(m) for m in models]


@app.get("/v1/models/{name}", tags=["registry"])
async def get_model_endpoint(name: str, version: str = "v1"):
    model = await registry.get_model(database, name, version)
    if not model:
        raise HTTPException(404, "Unknown model")
    return vars(model)


@app.post("/v1/models", tags=["registry"])
async def register_model_endpoint(payload: ModelRegistration, key=_admin_dep()):
    if payload.protocol not in ("http", "grpc"):
        raise HTTPException(400, "protocol must be 'http' or 'grpc'")
    try:
        model_id = await registry.register_model(database, **payload.model_dump())
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(409, f"Model {payload.name}:{payload.version} already registered")
        raise
    return {"id": model_id, "status": "registered"}


@app.patch("/v1/models/{model_id}", tags=["registry"])
async def update_model_endpoint(model_id: str, payload: ModelUpdate, key=_admin_dep()):
    model = await registry.get_model_by_id(database, model_id)
    if not model:
        raise HTTPException(404, "Model not found")
    await registry.update_model(database, model_id, **payload.model_dump(exclude_unset=True))
    return {"status": "updated"}


@app.delete("/v1/models/{model_id}", tags=["registry"])
async def disable_model_endpoint(model_id: str, key=_admin_dep()):
    model = await registry.get_model_by_id(database, model_id)
    if not model:
        raise HTTPException(404, "Model not found")
    await registry.disable_model(database, model_id)
    return {"status": "disabled"}


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
async def ready():
    try:
        await database.fetch_one("SELECT 1")
        return {"status": "ready"}
    except Exception as e:
        raise HTTPException(503, f"Not ready: {e}")
