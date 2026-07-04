# Phase 1 — Core Gateway

### ModelMesh Implementation Guide

**Goal:** Two models, one gateway, fully authenticated, rate-limited, logged — and routed entirely through a dynamic Model Registry rather than hardcoded model names. No queue, no streaming yet — that's intentional.

**Estimated time:** 3–4 days (the registry adds real time, but it saves you from rewriting routing logic in every later phase)

---

## 1. Repo structure

```
modelmesh/
├── gateway/
│   ├── main.py
│   ├── auth.py
│   ├── rate_limit.py
│   ├── registry.py          # NEW — Model Registry service
│   ├── schemas.py
│   ├── db.py
│   └── requirements.txt
├── workers/
│   ├── plant_health/
│   │   ├── inference.py
│   │   ├── model/            # your existing EfficientNet-B2 weights
│   │   └── requirements.txt
│   └── asr/
│       ├── inference.py
│       └── requirements.txt
├── tests/
│   ├── test_auth.py
│   ├── test_registry.py     # NEW
│   ├── test_plant_health.py
│   └── test_asr.py
├── docker-compose.yml
└── README.md
```

---

## 2. Database schema (Postgres)

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    key_hash TEXT UNIQUE NOT NULL,
    rate_limit_per_min INT DEFAULT 60,
    created_at TIMESTAMPTZ DEFAULT now(),
    revoked BOOLEAN DEFAULT FALSE
);

-- NEW: Model Registry
CREATE TABLE models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT 'v1',
    worker_name TEXT NOT NULL,
    protocol TEXT NOT NULL,          -- 'http' | 'grpc' (grpc arrives in Phase 3)
    endpoint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'disabled'
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(name, version)
);

CREATE TABLE requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id UUID REFERENCES api_keys(id),
    model_id UUID REFERENCES models(id),
    model_type TEXT NOT NULL,
    status TEXT NOT NULL,          -- 'success' | 'error' | 'rate_limited'
    latency_ms INT,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

Seed it with your two initial models:

```sql
INSERT INTO models (name, version, worker_name, protocol, endpoint, description) VALUES
('plant-health', 'v1', 'plant-worker', 'http', 'http://plant-health-worker:8001/infer', 'EfficientNet-B2 plant disease classifier'),
('asr', 'v1', 'asr-worker', 'http', 'http://asr-worker:8002/infer', 'Whisper-tiny speech-to-text');
```

The `requests` table now carries `model_id` alongside `model_type` — this is what lets Phase 5's ClickHouse dashboards break metrics down per model _and_ per version once canary deployments introduce a second version of the same model name.

---

## 3. Model Registry service (`registry.py`)

This is the core architectural addition. **The gateway must never know what "plant-health" or "asr" mean — it only knows how to ask the registry "where does this model live and how do I talk to it."**

```python
# gateway/registry.py
from dataclasses import dataclass

@dataclass
class ModelRecord:
    id: str
    name: str
    version: str
    worker_name: str
    protocol: str
    endpoint: str
    status: str

async def get_model(db, model_name: str, version: str = "v1") -> ModelRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM models WHERE name = :name AND version = :version",
        {"name": model_name, "version": version}
    )
    if not row:
        return None
    return ModelRecord(**row)

async def list_models(db):
    rows = await db.fetch_all("SELECT * FROM models ORDER BY name, version")
    return [ModelRecord(**r) for r in rows]

async def register_model(db, name, version, worker_name, protocol, endpoint, description=None):
    return await db.execute(
        """INSERT INTO models (name, version, worker_name, protocol, endpoint, description)
           VALUES (:name, :version, :worker_name, :protocol, :endpoint, :description)
           RETURNING id""",
        locals()
    )

async def update_model(db, model_id: str, **fields):
    # build a dynamic SET clause from provided fields (status, endpoint, version, protocol)
    ...

async def disable_model(db, model_id: str):
    await db.execute("UPDATE models SET status = 'disabled' WHERE id = :id", {"id": model_id})
```

No SQL lives anywhere outside this file. The gateway's routing code only ever calls `registry.get_model(...)`.

---

## 4. Registry management endpoints

```python
# gateway/main.py (registry routes)

@app.get("/v1/models")
async def list_models_endpoint():
    return await registry.list_models(db)

@app.get("/v1/models/{name}")
async def get_model_endpoint(name: str, version: str = "v1"):
    model = await registry.get_model(db, name, version)
    if not model:
        raise HTTPException(404, "Unknown model")
    return model

@app.post("/v1/models")
async def register_model_endpoint(payload: ModelRegistration, key=Depends(verify_admin_key)):
    return await registry.register_model(db, **payload.dict())

@app.patch("/v1/models/{model_id}")
async def update_model_endpoint(model_id: str, payload: ModelUpdate, key=Depends(verify_admin_key)):
    return await registry.update_model(db, model_id, **payload.dict(exclude_unset=True))

@app.delete("/v1/models/{model_id}")
async def disable_model_endpoint(model_id: str, key=Depends(verify_admin_key)):
    await registry.disable_model(db, model_id)
    return {"status": "disabled"}
```

`verify_admin_key` is a stricter auth dependency than your regular API key check — registering/disabling models shouldn't be doable by any authenticated client, only an admin-scoped key. Reuse your existing JWT/role pattern from the Zynthora RBAC work here.

---

## 5. Gateway routing — now fully dynamic

Replace any hardcoded `if model_type == "plant-health"` with a registry lookup:

```python
@app.post("/v1/infer/{model_name}")
async def infer(model_name: str, file: UploadFile, key=Depends(verify_api_key)):
    start = time.perf_counter()
    await check_rate_limit(key.id, key.rate_limit_per_min, redis)

    model = await registry.get_model(db, model_name)
    if not model:
        raise HTTPException(404, "Unknown model")
    if model.status != "active":
        raise HTTPException(503, "Model is currently disabled")

    payload = await file.read()
    result = await route_request(endpoint=model.endpoint, protocol=model.protocol, payload=payload)

    latency_ms = int((time.perf_counter() - start) * 1000)
    await log_request(key.id, model.id, model_name, "success", latency_ms)
    return result
```

```python
async def route_request(endpoint: str, protocol: str, payload: bytes):
    if protocol == "http":
        async with httpx.AsyncClient() as client:
            resp = await client.post(endpoint, content=payload)
            return resp.json()
    elif protocol == "grpc":
        raise NotImplementedError("gRPC routing arrives in Phase 3")
    else:
        raise ValueError(f"Unsupported protocol: {protocol}")
```

Note the `protocol` branch already anticipates Phase 3 — when you add gRPC, you're extending `route_request`, not rewriting the endpoint handler. This is the actual payoff of building the registry now: every later phase plugs in underneath it instead of triggering a rewrite.

---

## 6. Workers (unchanged in shape, registered instead of hardcoded)

### Plant Health worker

Reuse your existing EfficientNet-B2 model. Wrap it in a clean function with no gateway logic inside it:

```python
# workers/plant_health/inference.py
def predict(image_bytes: bytes) -> dict:
    image = preprocess(image_bytes)
    with torch.no_grad():
        output = model(image)
    return {"class": CLASSES[output.argmax()], "confidence": float(output.softmax(-1).max())}
```

### ASR worker

```bash
pip install openai-whisper
```

```python
# workers/asr/inference.py
import whisper
model = whisper.load_model("tiny")

def transcribe(audio_path: str) -> dict:
    result = model.transcribe(audio_path)
    return {"text": result["text"]}
```

Adding a **third** model later (say, OCR) means: build the worker, deploy it, `POST /v1/models` to register it. Zero gateway code changes. That's the whole point of this section — verify it by actually trying this once Phase 1 is done, before moving to Phase 2.

---

## 7. Caching (Redis) — unchanged

```python
async def get_cached_or_infer(input_bytes, model_name, infer_fn):
    cache_key = f"cache:{model_name}:{hashlib.sha256(input_bytes).hexdigest()}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)
    result = infer_fn(input_bytes)
    await redis.set(cache_key, json.dumps(result), ex=3600)
    return result
```

---

## 8. Docker Compose

```yaml
version: "3.9"
services:
  gateway:
    build: ./gateway
    ports: ["8000:8000"]
    depends_on: [postgres, redis]
    environment:
      - DATABASE_URL=postgresql://user:pass@postgres/modelmesh
      - REDIS_URL=redis://redis:6379

  plant-health-worker:
    build: ./workers/plant_health
    ports: ["8001:8001"]

  asr-worker:
    build: ./workers/asr
    ports: ["8002:8002"]

  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: modelmesh
      POSTGRES_PASSWORD: pass
    volumes: ["pgdata:/var/lib/postgresql/data"]

  redis:
    image: redis:7

volumes:
  pgdata:
```

Note the worker service names (`plant-health-worker`, `asr-worker`) must match what you put in the `models.endpoint` column when you seed the registry.

---

## 9. Tests

```python
# tests/test_registry.py
async def test_lookup_known_model(db):
    model = await registry.get_model(db, "plant-health")
    assert model.status == "active"

async def test_lookup_unknown_model(db):
    model = await registry.get_model(db, "does-not-exist")
    assert model is None

async def test_disabled_model_returns_503(client, valid_key):
    await registry.disable_model(db, plant_health_model_id)
    resp = await client.post("/v1/infer/plant-health", headers=auth_header(valid_key))
    assert resp.status_code == 503

async def test_duplicate_registration_rejected(db):
    with pytest.raises(Exception):  # unique constraint on (name, version)
        await registry.register_model(db, name="plant-health", version="v1", ...)

async def test_admin_only_can_register(client, non_admin_key):
    resp = await client.post("/v1/models", json={...}, headers=auth_header(non_admin_key))
    assert resp.status_code == 403
```

```python
# tests/test_auth.py — unchanged from before
async def test_rejects_missing_key(client):
    resp = await client.post("/v1/infer/plant-health")
    assert resp.status_code == 401

async def test_rate_limit_enforced(client, valid_key):
    for _ in range(61):
        resp = await client.post("/v1/infer/plant-health", headers=auth_header(valid_key))
    assert resp.status_code == 429
```

---

## 10. Load test (Artillery) — unchanged

```yaml
config:
  target: "http://localhost:8000"
  phases:
    - duration: 30
      arrivalRate: 5
      name: baseline
    - duration: 30
      arrivalRate: 20
      name: realistic
    - duration: 15
      arrivalRate: 100
      name: spike
scenarios:
  - name: "Plant health inference"
    flow:
      - post:
          url: "/v1/infer/plant-health"
          headers:
            Authorization: "Bearer {{ apiKey }}"
```

Run it and **write down your p50/p95/p99 latency numbers** — this is still your Phase 3 comparison baseline. The registry lookup adds a small amount of latency (one extra DB query per request); it's worth noting that number honestly too, and considering whether it needs caching later (it's a good candidate for the Redis cache you already have).

---

## 11. Definition of done

- [ ] `docker compose up` brings up gateway + both workers + Postgres + Redis
- [ ] No hardcoded `if model_type == ...` exists anywhere in gateway code
- [ ] `/v1/infer/{model_name}` resolves routing entirely through `registry.get_model()`
- [ ] `GET /v1/models`, `GET /v1/models/{name}`, `POST /v1/models`, `PATCH /v1/models/{id}`, `DELETE /v1/models/{id}` all work
- [ ] Registering a third model end-to-end (new worker + `POST /v1/models`) requires zero changes to gateway route handlers
- [ ] Unknown model → 404, disabled model → 503
- [ ] Invalid/missing API keys are rejected with 401; rate limiting returns 429
- [ ] Every request logged to `requests` with `model_id` and real latency
- [ ] Repeated identical requests hit Redis cache, not the model
- [ ] pytest suite passes, including registry tests
- [ ] Artillery baseline numbers recorded (with registry lookup included in the measured path)

Once every box is checked, move to Phase 2.
