# ModelMesh Setup Guide

Complete setup instructions for running ModelMesh locally with Docker Compose or Kubernetes.

---

## Prerequisites

**WSL2 Ubuntu required** (Windows users). Model caches split across filesystems if run on Windows Python.

### Install Models

```bash
pip install easyocr openai-whisper
sudo apt install ffmpeg -y  # Required for Whisper audio decoding
```

### Verify Downloads

```bash
python3 << 'EOF'
import easyocr
import whisper

print("Downloading EasyOCR models...")
ocr_reader = easyocr.Reader(['en'], gpu=False)
print("EasyOCR ready")

print("Downloading Whisper tiny model...")
asr_model = whisper.load_model("tiny")
print("Whisper ready")

print("\nBoth models cached successfully (~140MB total)")
EOF
```

Models cached to `~/.EasyOCR/model/` and `~/.cache/whisper/`. First run: 2-3 minutes. Subsequent: instant.

---

## Docker Compose (Development)

Use [RUNNING.md](RUNNING.md) for first build, later starts, rebuilds, and stop/resume commands. The gateway is available at `http://localhost:8000`; API docs are at `http://localhost:8000/docs`.

### Reliability environment variables

| Variable | Default | Used by |
|---|---:|---|
| `MAX_UPLOAD_BYTES` | `10485760` | gateway and workers |
| `PAYLOAD_TTL_SECONDS` | `3600` | gateway and workers |
| `STREAM_MAXLEN` | `10000` | gateway and workers |
| `RECLAIM_IDLE_MS` | `300000` | workers |
| `WORKER_NAME` | service-specific | workers |
| `GRPC_MAX_WORKERS` | `10` locally | workers |
| `CLICKHOUSE_HOST` | `clickhouse` | worker metrics |
| `CLICKHOUSE_PORT` | `8123` | worker metrics |

Kubernetes worker pods set `GRPC_MAX_WORKERS=3` because they are limited to one CPU.

ClickHouse is not included in Docker Compose; it is available in the Kubernetes deployment. Each worker writes exactly one metric row when a job reaches a final outcome (success or terminal failure); retry attempts that continue do not produce rows. The gateway does not write metrics, and jobs that never finish produce no row. Worker inserts are best-effort: a failure does not fail inference, and that worker skips further inserts for 60 seconds after one warning.

---

## Kubernetes (minikube)

### 1. Start minikube

```bash
minikube start --cpus=2 --memory=4096
minikube addons enable metrics-server
```

### 2. Build images in minikube's Docker

```bash
eval $(minikube docker-env)
docker build -t modelmesh-gateway:latest -f gateway/Dockerfile .
docker build -t modelmesh-doc-ocr:latest -f workers/doc_ocr/Dockerfile .
docker build -t modelmesh-asr:latest -f workers/asr/Dockerfile .
```

### 3. Deploy to Kubernetes

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/redis/
kubectl apply -f k8s/postgres/
kubectl apply -f k8s/gateway/
kubectl apply -f k8s/workers/
```

### 4. Check status

```bash
kubectl get pods -n modelmesh
kubectl get svc -n modelmesh
```

### 5. Access gateway

```bash
kubectl port-forward -n modelmesh svc/gateway 8000:8000
curl http://localhost:8000/v1/models
```

The gateway Service listens on port 8000.

The database Secret manifest is `k8s/secrets.yaml`; the gateway JWT Secret manifest is `k8s/gateway/jwt-secret.yaml` (also applied by `kubectl apply -f k8s/gateway/`).

---

## Authentication

### Register user

```bash
curl -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secure_pass"}'
```

### Login (returns JWT)

```bash
JWT=$(curl -X POST http://localhost:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secure_pass"}' \
  | jq -r .access_token)
```

### Issue API key

```bash
API_KEY=$(curl -X POST http://localhost:8000/v1/auth/api-keys \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"rate_limit_per_min": 100}' \
  | jq -r .key)
```

### Admin key bootstrap

Registry management endpoints require an **admin API key**, not a JWT. After creating an API key, promote it in Postgres using the raw key. Do not share or commit the raw key:

```bash
printf "UPDATE api_keys SET is_admin = TRUE WHERE key_hash = encode(digest('%s','sha256'),'hex') RETURNING id, is_admin;\n" "$API_KEY" \
  | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U user -d modelmesh
export ADMIN_KEY="$API_KEY"
```

Keep using the same shell after exporting `ADMIN_KEY`; do not print or commit it. The raw key is returned only when the API key is created.

To make the key available to later WSL shells, save it once in the git-ignored `.env`:

```bash
test -n "${ADMIN_KEY:-}" || { echo "ADMIN_KEY is empty"; exit 1; }
printf '\nADMIN_KEY=%s\n' "$ADMIN_KEY" >> .env
```

In a new WSL shell at the repository root, load local settings without displaying them:

```bash
set -a
. ./.env
set +a
```

Use the admin key as a Bearer token for registry endpoints:

```bash
curl -X PATCH http://localhost:8000/v1/models/<model-id> \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"description":"Updated description"}'
```

## Canary rollback guard

The optional `gateway/canary_guard.py` script checks active canaries against final-outcome ClickHouse metrics. It is a dry run by default. It requires ClickHouse only when a positive-percentage canary exists; for a temporary local ClickHouse, set `CLICKHOUSE_HOST=localhost`. Kubernetes uses the default service name `clickhouse`. When the highest active version is already at 0%, it skips ClickHouse and reports `noop`.

Defaults: 10-minute sliding window, 30 minimum requests per version, and a 0.10 error-rate delta. The guard checks both canary starvation and whether the canary error rate exceeds stable by more than that delta.

```bash
# Dry run: prints one JSON line per active version pair and changes nothing.
GATEWAY_URL=http://localhost:8000 CLICKHOUSE_HOST=localhost \
  python gateway/canary_guard.py

# Apply only a rollback decision; ADMIN_KEY must be exported in this shell.
GATEWAY_URL=http://localhost:8000 CLICKHOUSE_HOST=localhost \
  python gateway/canary_guard.py --apply
```

`--apply` only sets the selected canary's `canary_percent` to `0`; it never disables or deletes a model. Do not pass the admin key as a command-line argument.

See [Phase 5 observability and canary documentation](docs/Phase5-Observability-Canary.md#canary-rollback-guard-optional-phase-11) for the rules, limitations, and verification notes.

## Sample fixtures

Create the ignored OCR image and silence audio fixtures using the commands in [Phase 0, Step 0.4](docs/00_Run_The_Project_Prompt.md#step-04-create-sample-files). The silence fixture verifies pipeline plumbing, not transcription quality.

---

## Usage Examples

### Async Inference

```bash
# Submit job
JOB_ID=$(curl -X POST http://localhost:8000/v1/infer/doc-ocr \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@document.jpg" \
  | jq -r .job_id)

# Poll status
curl http://localhost:8000/v1/jobs/$JOB_ID \
  -H "Authorization: Bearer $API_KEY"
```

### WebSocket Streaming

```bash
# Python CLI
python test_ws_client.py $API_KEY asr audio.wav

# Browser: Open test_ws_client.html
```

### Model Registry Management

```bash
# List models
curl http://localhost:8000/v1/models

# Register new model (admin only)
curl -X POST http://localhost:8000/v1/models \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "custom-nlp",
    "version": "v1",
    "worker_name": "nlp-worker",
    "protocol": "grpc",
    "endpoint": "nlp-worker:50051"
  }'

# Update protocol (HTTP ↔ gRPC switch)
curl -X PATCH http://localhost:8000/v1/models/<model-id> \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"protocol": "grpc", "endpoint": "doc-ocr-worker:50051"}'
```

---

## Testing

### Unit + Integration Tests

The default Compose file does not publish Postgres or Redis ports. From Ubuntu WSL at the repository root, start Compose with the test-only override and use only the scratch database and Redis DB 1:

```bash
cd /mnt/c/Project_Files/modelmesh
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d

# Skip only this CREATE command if modelmesh_test already exists. Never drop it.
docker compose exec -T postgres psql -U user -d postgres -c "CREATE DATABASE modelmesh_test;"
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U user -d modelmesh_test < db/init.sql
for f in migrations/*.sql; do
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U user -d modelmesh_test < "$f"
done

export TEST_DATABASE_URL=postgresql://user:pass@localhost/modelmesh_test
export TEST_REDIS_URL=redis://localhost:6379/1
source "$HOME/modelmesh/modelmesh-env/bin/activate"
pip install --dry-run -r tests/requirements.txt
pip install -r tests/requirements.txt
pytest -q
pytest -q
deactivate
```

If `modelmesh_test` already exists, keep it and use it as the scratch test database; skip only the `CREATE DATABASE` command. The idempotent schema and migration commands may be rerun; do not drop the database or use the application database `modelmesh`. The tests flush Redis DB 1 only. Test dependencies pin the existing NumPy 2.4.4 and OpenCV headless 5.0.0.93 versions; they do not downgrade them.

The working WSL virtualenv must be on the Linux filesystem. On this workstation it is `$HOME/modelmesh/modelmesh-env`; a copy under `/mnt/c` can contain empty Python launcher files and should not be activated.

Only the initial `up` needs both `-f` flags. After that, `docker compose ps`, `stop`, `start`, and `down` work normally. Plain `docker compose up -d` recreates Postgres and Redis without host ports, so use the override form again whenever tests need to connect. If Postgres or Redis are recreated while the app is running, reconnect the app services with:

```bash
docker compose restart gateway doc-ocr-worker asr-worker
```

The test harness generates protobuf modules into a temporary directory at startup; it does not add generated source files to the repository.

`test_disabled_model_returns_503` is an expected xfail until Phase 8 fixes disabled-model routing through the canary path.

**Expected: 20 passed, 1 xfailed.**

### Load Testing (Artillery)

```bash
npm install -g artillery

# Create load-test-vars.csv with valid API key
echo "apiKey" > load-test-vars.csv
echo "$API_KEY" >> load-test-vars.csv

artillery run load-test.yml
```

---

## Monitoring

### Redis Streams

```bash
docker compose exec redis redis-cli

XLEN inference_jobs              # Queue depth
XPENDING inference_jobs doc-ocr-worker_group  # Pending jobs
XREAD COUNT 10 STREAMS inference_jobs_dlq 0   # Dead-letter queue
```

### Kubernetes

```bash
kubectl get pods -n modelmesh -w
kubectl logs -n modelmesh -f deployment/gateway
kubectl top pods -n modelmesh
```

### ClickHouse Analytics

ClickHouse and its metrics table are Kubernetes-only; the Docker Compose stack does not include ClickHouse. Both workers write one row per completed job. See [`clickhouse/init.sql`](clickhouse/init.sql) for the table definition. Configure worker connections with `CLICKHOUSE_HOST` and `CLICKHOUSE_PORT`.

```sql
SELECT model_name, model_version,
       count() AS successful_jobs,
       avg(latency_ms) AS avg_latency_ms,
       quantile(0.95)(latency_ms) AS p95_latency_ms
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR
  AND status = 'success'
GROUP BY model_name, model_version;
```

Error rate uses all final rows (both successes and errors):

```sql
SELECT model_name, model_version,
       countIf(status = 'error') / count() AS error_rate
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR
GROUP BY model_name, model_version;
```

---

## Troubleshooting

### Model cache issues

- Run setup in WSL2 Ubuntu, not Windows Python
- Models download to `~/.EasyOCR/model/` and `~/.cache/whisper/`

### Docker build errors

- Run `eval $(minikube docker-env)` before building for Kubernetes
- Check build context is project root in docker-compose.yml

### WebSocket connection refused

- Check gateway is running: `curl http://localhost:8000/health`
- Verify API key is valid
- Check WebSocket endpoint exists

### gRPC UNAVAILABLE error

- Check worker gRPC server: `docker-compose logs doc-ocr-worker | grep "gRPC server started"`
- Verify port mappings in docker-compose.yml
- Check registry endpoint matches Service name in Kubernetes

---

For detailed phase-by-phase documentation, see [docs/](docs/) directory.
