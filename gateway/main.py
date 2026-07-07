import hashlib
import json
import time
import random

import httpx
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header, WebSocket, WebSocketDisconnect

from db import database, redis, connect, disconnect
from auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_api_key,
    decode_token,
    make_verify_api_key,
    make_verify_admin_key,
)
from rate_limit import check_rate_limit
import registry
from job_queue import enqueue_job, get_job_status as _get_job_status
from grpc_client import call_worker_grpc
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
    async def inner(authorization: str | None = Header(None)):
        return await verify_api_key(authorization)
    return Depends(inner)


def _admin_dep():
    async def inner(authorization: str | None = Header(None)):
        return await verify_admin_key(authorization)
    return Depends(inner)


def _jwt_dep():
    """Verify JWT token and return user_id — used for issuing API keys."""
    async def inner(authorization: str | None = Header(None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Missing or invalid Authorization header")
        token = authorization.removeprefix("Bearer ").strip()
        user_id = decode_token(token)
        if not user_id:
            raise HTTPException(401, "Invalid or expired token")
        return user_id
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


async def route_request(endpoint: str, protocol: str, payload: bytes, filename: str = "file", model_name: str = ""):
    if protocol == "http":
        # Determine content-type/filename so workers that validate MIME type
        # (e.g. doc-ocr requiring image/*) don't reject the request.
        if "ocr" in model_name:
            content_type = "image/jpeg"
            filename = filename if filename != "file" else "upload.jpg"
        elif model_name == "asr":
            content_type = "audio/wav"
            filename = filename if filename != "file" else "upload.wav"
        else:
            content_type = "application/octet-stream"

        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"file": (filename, payload, content_type)}
            resp = await client.post(endpoint, files=files)
            resp.raise_for_status()
            return resp.json()
    elif protocol == "grpc":
        return await call_worker_grpc(endpoint, model_name, payload)
    else:
        raise ValueError(f"Unsupported protocol: {protocol}")


async def get_model_with_canary(db, model_name: str):
    """
    Registry-driven canary routing. When more than one active version of a
    model exists, the highest version string is treated as the canary
    candidate and routed to based on its own canary_percent field — no
    hardcoded config, no separate dict, purely data-driven per Phase 1's
    design principle.
    """
    versions = await registry.list_versions(db, model_name)
    if not versions:
        return None
    if len(versions) == 1:
        return versions[0]
    canary = max(versions, key=lambda m: m.version)
    stable = min(versions, key=lambda m: m.version)
    roll = random.randint(1, 100)
    return canary if roll <= canary.canary_percent else stable


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
async def issue_api_key(payload: ApiKeyCreate, user_id=_jwt_dep()):
    raw_key = await create_api_key(database, user_id, payload.rate_limit_per_min)
    return {"key": raw_key, "rate_limit_per_min": payload.rate_limit_per_min}


# ── inference endpoint ────────────────────────────────────────────────────────

@app.post("/v1/infer/{model_name}", tags=["inference"])
async def infer(
    model_name: str,
    file: UploadFile = File(...),
    version: str | None = None,
    key=_key_dep(),
):
    await check_rate_limit(key.id, key.rate_limit_per_min, redis)

    if version is None:
        # No version pinned by the caller — apply registry-driven canary routing.
        model = await get_model_with_canary(database, model_name)
    else:
        # Caller explicitly pinned a version (e.g. ?version=v1) — always honor it,
        # bypassing canary logic entirely. This is what lets some traffic stay
        # deliberately on stable during a rollout.
        model = await registry.get_model(database, model_name, version)

    if not model:
        raise HTTPException(404, "Unknown model")
    if model.status != "active":
        raise HTTPException(503, "Model is currently disabled")

    payload_bytes = await file.read()

    job_id = await enqueue_job(redis, model, payload_bytes, key.id)

    return {
        "job_id": job_id,
        "status": "queued",
        "model": model.name,
        "version": model.version,
    }


@app.get("/v1/jobs/{job_id}", tags=["inference"])
async def get_job_status(job_id: str, key=_key_dep()):
    result = await _get_job_status(redis, job_id)
    if not result:
        raise HTTPException(404, "Job not found or expired")
    return result


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


# ── WebSocket streaming endpoint ─────────────────────────────────────────────

@app.websocket("/v1/ws/infer/{model_name}")
async def ws_infer(websocket: WebSocket, model_name: str):
    """
    WebSocket endpoint for streaming inference.

    Query params:
        api_key: API key for authentication
        version: Model version (default: v1)

    Sends partial results as JSON messages: {"partial": result, "is_final": bool}
    """
    await websocket.accept()

    try:
        # Authenticate via query param
        api_key = websocket.query_params.get("api_key")
        if not api_key:
            await websocket.send_json({"error": "Missing api_key query parameter"})
            await websocket.close(code=4401)
            return

        # Verify API key
        key_record = await database.fetch_one(
            "SELECT id, user_id, rate_limit_per_min FROM api_keys WHERE key_hash = :hash",
            {"hash": hashlib.sha256(api_key.encode()).hexdigest()}
        )

        if not key_record:
            await websocket.send_json({"error": "Invalid API key"})
            await websocket.close(code=4401)
            return

        # Rate limiting
        await check_rate_limit(key_record["id"], key_record["rate_limit_per_min"], redis)

        # Resolve model from registry
        version = websocket.query_params.get("version", "v1")
        model = await registry.get_model(database, model_name, version)

        if not model:
            await websocket.send_json({"error": "Unknown model"})
            await websocket.close(code=4404)
            return

        if model.status != "active":
            await websocket.send_json({"error": "Model is currently disabled"})
            await websocket.close(code=4503)
            return

        # Send ready signal
        await websocket.send_json({"status": "ready", "model": model.name, "protocol": model.protocol})

        # Handle streaming based on protocol
        if model.protocol == "grpc" and model.supports_streaming:
            from grpc_client import stream_worker_grpc

            chunk_count = 0

            async def chunk_generator():
                nonlocal chunk_count
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        chunk_count += 1
                        is_final = True  # simplified: one chunk for now
                        yield data, chunk_count, is_final
                        if is_final:
                            break
                except WebSocketDisconnect:
                    pass

            async for partial_result in stream_worker_grpc(model.endpoint, model.name, chunk_generator()):
                await websocket.send_json({
                    "partial": partial_result.get("text", ""),
                    "is_final": partial_result.get("is_final", False),
                    "confidence": partial_result.get("confidence", 0.0)
                })

        else:
            # Covers: http protocol, AND grpc protocol without streaming support (e.g. doc-ocr)
            data = await websocket.receive_bytes()
            result = await route_request(model.endpoint, model.protocol, data, model_name=model.name)
            await websocket.send_json({"result": result, "is_final": True})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"error": str(e)})
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass