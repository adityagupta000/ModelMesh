# Phase 1 — Core Gateway

## Implementation Status: COMPLETE ✓

**Goal:** Two models, one gateway, authenticated and rate-limited, routed through a dynamic Model Registry.

**Actual time:** Completed

---

## What Was Implemented

### Core Features (All Complete)

- EasyOCR document text extraction + Whisper speech-to-text
- Registry-backed worker assignment; no hardcoded model types in gateway routing
- JWT auth + bcrypt passwords + hashed API keys
- Redis fixed-window rate limiting per key
- Admin-only registry management endpoints
- Docker Compose setup with health checks
- pytest test suite

### Architecture

```
Client → Gateway (FastAPI) → Worker (HTTP)
         ↓
         PostgreSQL (users, API keys, models; requests table is not currently written)
         Redis (rate limiting; no result cache)
```

---

## Definition of Done

- [x] EasyOCR and Whisper working on real test files
- [x] `docker compose up` brings up all services
- [x] `/v1/infer/{model_name}` routes via registry
- [x] All registry CRUD endpoints work
- [x] Adding a third model requires zero code changes for HTTP content-type routing
- [x] Error handling (404 unknown, 503 disabled, 401 auth, 429 rate limit)
- [ ] pytest suite passes (see the Phase 0 baseline; test repairs are in Phase 7)

---

## Key Design Decisions

**Model Registry Pattern**: The gateway resolves model metadata and asynchronous worker assignment from the database. HTTP routing sends octet-stream payloads and worker implementations perform model-specific decoding. The WebSocket path reads protocol from the registry; REST jobs do not.

**Trade-off**: One extra DB query per request. Worth it for flexibility gained in later phases.

---

## Files Implemented

- `gateway/main.py` - FastAPI app with auth + routing
- `gateway/auth.py` - JWT + API key verification
- `gateway/rate_limit.py` - Redis-based rate limiting
- `gateway/registry.py` - Model Registry service
- `gateway/schemas.py` - Pydantic models
- `gateway/db.py` - Database connection
- `workers/doc_ocr/inference.py` - EasyOCR wrapper
- `workers/asr/inference.py` - Whisper wrapper
- `db/init.sql` - Database schema
- `docker-compose.yml` - Service orchestration
- `tests/*` - Auth, registry, worker tests

---

## Next Phase

Phase 2: Async Job Queue (Redis Streams; Kafka code is dormant)
