# ModelMesh — Phase 1: Core Gateway

Two models (EasyOCR document OCR + Whisper ASR), one gateway, fully authenticated, rate-limited, logged, and routed through a dynamic Model Registry.

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

## What this implements

Per [Phase1-Core-Gateway.md](../docs/Phase1-Core-Gateway.md):

- ✅ **EasyOCR** (document text extraction) + **Whisper** (speech-to-text)
- ✅ Dynamic **Model Registry** — zero hardcoded model names in gateway
- ✅ JWT auth + bcrypt passwords + hashed API keys
- ✅ Redis rate limiting (sliding window per key)
- ✅ Redis result caching (keyed by model_id + SHA256(payload))
- ✅ Every request logged to `requests` table with `model_id` + latency
- ✅ Admin-only registry endpoints (`is_admin` column on `api_keys`)
- ✅ `protocol` field ready for Phase 3 gRPC (stub already in `route_request`)
- ✅ Docker Compose with health checks
- ✅ Full pytest suite

## Definition of done checklist

- [ ] EasyOCR and Whisper both installed, downloaded, and verified working on real test files
- [ ] `docker compose up` brings up gateway + both workers + Postgres + Redis
- [ ] No hardcoded `if model_type == ...` anywhere in gateway code
- [ ] `/v1/infer/{model_name}` resolves routing entirely through `registry.get_model()`
- [ ] All five registry CRUD endpoints work
- [ ] Registering a third model requires zero gateway code changes
- [ ] Unknown model → 404, disabled model → 503
- [ ] Missing/invalid API key → 401; rate limit → 429
- [ ] Every request logged to `requests` with `model_id` and real latency
- [ ] Repeated identical requests hit Redis cache, not the model
- [ ] `pytest` suite passes
- [ ] Artillery baseline numbers recorded
