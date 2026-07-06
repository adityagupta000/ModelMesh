# ModelMesh — Multi-Model Inference Gateway

A production-ready model serving platform with async job queues, dynamic model registry, and comprehensive observability.

**Current Status**: Phase 2 Complete (Async Job Queue with Redis Streams + Kafka)

## Prerequisites

**Run all setup in WSL2 Ubuntu, not Windows Python** to avoid cache split across filesystems.

### Install and verify both models BEFORE running docker-compose

```bash
# Install
pip install easyocr openai-whisper
sudo apt install ffmpeg -y

# Test
python3 << 'EOF'
import easyocr
import whisper

print("Downloading EasyOCR models...")
ocr_reader = easyocr.Reader(['en'], gpu=False)
print("EasyOCR ready ✓")

print("Downloading Whisper tiny model...")
asr_model = whisper.load_model("tiny")
print("Whisper ready ✓")

print("\nBoth models downloaded and loaded successfully.")
EOF
```

This downloads ~140MB of weights to `~/.EasyOCR/model/` and `~/.cache/whisper/`. First run takes a few minutes; subsequent runs are instant.

## Quick start

```bash
cp .env.example .env          # fill in JWT_SECRET at minimum
docker compose up --build
```

Gateway is now live at `http://localhost:8000`. Interactive docs: `http://localhost:8000/docs`

## Register a user and get an API key

```bash
# Register
curl -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"yourpass"}'

# Login
curl -X POST http://localhost:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"yourpass"}'
# → {"access_token": "<jwt>"}

# Issue an API key (use the JWT as Bearer)
curl -X POST http://localhost:8000/v1/auth/api-keys \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"rate_limit_per_min": 60}'
# → {"key": "<raw-key>", "rate_limit_per_min": 60}
```

## Inference

```bash
# Document OCR
curl -X POST http://localhost:8000/v1/infer/doc-ocr \
  -H "Authorization: Bearer <raw-key>" \
  -F "file=@document.jpg"

# ASR
curl -X POST http://localhost:8000/v1/infer/asr \
  -H "Authorization: Bearer <raw-key>" \
  -F "file=@audio.wav"
```

## Model Registry

```bash
# List all models
curl http://localhost:8000/v1/models

# Register a third model (admin key required)
curl -X POST http://localhost:8000/v1/models \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"name":"custom-ocr","version":"v1","worker_name":"custom-ocr-worker","protocol":"http","endpoint":"http://custom-ocr-worker:8003/infer"}'

# Disable a model
curl -X DELETE http://localhost:8000/v1/models/<model-id> \
  -H "Authorization: Bearer <admin-key>"
```

## Running tests

```bash
# Start test Postgres + Redis first (or point TEST_DATABASE_URL at your instance)
docker compose up postgres redis -d

# Create test DB
psql postgresql://user:pass@localhost/postgres -c "CREATE DATABASE modelmesh_test;"
psql postgresql://user:pass@localhost/modelmesh_test < db/init.sql

pip install -r tests/requirements.txt
cd modelmesh
pytest
```

## Load test (Artillery)

```bash
npm install -g artillery
# Put a valid API key in load-test-vars.csv: apiKey
artillery run load-test.yml
```

Record your p50/p95/p99 numbers — these are your Phase 3 baseline.

## Phase 1: Core Gateway (Complete)

The foundation — two models (EasyOCR + Whisper), dynamic model registry, authentication, rate limiting, and logging.

### Features Implemented

- **EasyOCR** (document text extraction) + **Whisper** (speech-to-text)
- Dynamic **Model Registry** — zero hardcoded model names in gateway
- JWT auth + bcrypt passwords + hashed API keys
- Redis rate limiting (sliding window per key)
- Redis result caching (keyed by model_id + SHA256(payload))
- Request logging to database with model_id + latency
- Admin-only registry endpoints
- Docker Compose with health checks
- Full pytest suite

### Architecture

```
Client → Gateway (FastAPI) → Worker (HTTP)
         ↓
         PostgreSQL (users, API keys, models, requests)
         Redis (rate limiting, caching)
```

---

## Phase 2: Async Job Queue (Complete)

Decoupled gateway from workers using Redis Streams and Kafka. Jobs are queued, workers process asynchronously, clients poll for results.

### Features Implemented

- **Redis Streams** job queue with consumer groups
- **Kafka** alternative implementation (KRaft mode, no Zookeeper)
- Async `/v1/infer/{model_name}` endpoint (returns job_id immediately)
- `/v1/jobs/{job_id}` status polling endpoint
- Worker consumers filter by `worker_name` from registry
- **Retry logic**: Max 3 retries with exponential backoff (2s, 4s, 8s)
- **Dead-letter queue**: Failed jobs moved to `inference_jobs_dlq`
- **Consumer lag monitoring**: Stream length and pending count logged
- **Idempotency**: Safe redelivery on worker crash
- Job status TTLs (1 hour for status, 2 hours for results)
- Horizontal worker scaling support

### Architecture

```
Client → Gateway → Redis Streams / Kafka → Workers (async)
         ↓                                    ↓
         Returns job_id                       Store result in Redis

Client → Gateway → Poll /v1/jobs/{job_id} → Get result
```

### Key Design Decisions

**Why async queue?**

- Gateway never blocks on slow inference
- Workers scale independently of gateway
- Automatic retries on transient failures
- Better fault tolerance (worker crashes don't lose work)

**Trade-off**: Clients must poll for results (Phase 3 adds WebSockets to eliminate polling)

**Redis Streams vs Kafka**:
| Aspect | Redis Streams | Kafka |
|--------|---------------|-------|
| Setup | Simple, reuse existing Redis | Requires Kafka broker |
| Ops overhead | Low | Higher |
| Throughput | ~10-50k msgs/sec | 100k+ msgs/sec |
| Durability | Good (AOF/RDB) | Excellent (purpose-built) |
| Replay | Limited | First-class |
| **Use when** | Moderate scale, lower ops burden | High volume, need replay/multi-consumer |

**Current default**: Redis Streams (simpler, sufficient for current scale)

### Usage

#### Submit a job (async)

```bash
curl -X POST http://localhost:8000/v1/infer/doc-ocr \
  -H "Authorization: Bearer <api-key>" \
  -F "file=@document.jpg"

# Response:
{
  "job_id": "a1b2c3d4-...",
  "status": "queued",
  "model": "doc-ocr",
  "version": "v1"
}
```

#### Poll for result

```bash
curl http://localhost:8000/v1/jobs/a1b2c3d4-... \
  -H "Authorization: Bearer <api-key>"

# Response (when complete):
{
  "job_id": "a1b2c3d4-...",
  "status": "success",
  "result": {
    "text": "Extracted text...",
    "confidence": 0.95
  }
}
```

#### Job status progression

1. **queued**: In queue, not yet picked up
2. **processing**: Worker is processing
3. **success**: Completed successfully
4. **failed**: Failed after 3 retries (check DLQ)

### Monitoring

#### Check consumer lag (Redis Streams)

```bash
docker-compose exec redis redis-cli

# Stream length
XLEN inference_jobs

# Pending per consumer group
XPENDING inference_jobs doc-ocr-worker_group

# View dead-letter queue
XREAD COUNT 10 STREAMS inference_jobs_dlq 0
```

#### Scale workers horizontally

```bash
# Scale to 3 doc-ocr workers
docker-compose up --scale doc-ocr-worker=3

# Jobs automatically distributed via consumer groups
```

### Failure Handling

**Retry logic**:

- Worker failure → Job redelivered automatically
- Max 3 retries with exponential backoff
- After 3 failures → Move to dead-letter queue

**Idempotency**:

- Result storage uses Redis SETEX (idempotent)
- Safe to process same job multiple times

**Timeout behavior**:

- Jobs not completed in 1 hour → Status expires (404)
- Final results stored for 2 hours

### Switching to Kafka

To use Kafka instead of Redis Streams:

1. Update worker entrypoint scripts to run `kafka_consumer.py`
2. Update gateway to import and use `kafka_queue` module
3. Add Kafka dependency to docker-compose services
4. Rebuild: `docker-compose up --build`

See worker files: `consumer.py` (Redis), `kafka_consumer.py` (Kafka)

---

## Definition of Done Checklist

### Phase 1

- [x] EasyOCR and Whisper working on real test files
- [x] `docker compose up` brings up all services
- [x] No hardcoded model types in gateway
- [x] `/v1/infer/{model_name}` routes via registry
- [x] All registry CRUD endpoints work
- [x] Adding third model requires zero code changes
- [x] Error handling (404 unknown, 503 disabled, 401 auth, 429 rate limit)
- [x] Request logging with model_id and latency
- [x] Redis cache for repeated requests
- [x] pytest suite passes

### Phase 2

- [x] Gateway publishes jobs to Redis Streams
- [x] Job payload includes model_id, model_name, model_version, worker_name
- [x] Workers filter by worker_name (not hardcoded)
- [x] `/v1/jobs/{job_id}` returns real status
- [x] Retry logic with max 3 retries and DLQ
- [x] Consumer lag monitoring logged
- [x] Documented behavior for stale models
- [x] Kafka alternative implementation
- [x] Redis Streams vs Kafka comparison documented