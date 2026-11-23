# Getting started

From a fresh clone to a first inference, with Docker Compose (the main path) or minikube. Configuration, monitoring and troubleshooting are in [Operations](operations.md).

## Prerequisites

| Need                                 | For                                                                                                                           |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| Docker with the Compose plugin       | Running the stack                                                                                                             |
| About 10 GB of free disk             | Images: PyTorch (CPU) plus the EasyOCR and Whisper weights, which are **downloaded at image build time**                      |
| `curl` and `jq`                      | The examples and the smoke test                                                                                               |
| Several GiB of free memory           | The OCR and ASR containers each reported about 1.1 to 1.2 GiB at rest (see [Operations](operations.md#resource-sizing))       |
| Python 3.10 or newer on Linux or WSL | Running tests and the WebSocket client. Create the virtual environment on the Linux filesystem, not on a Windows-mounted path |
| minikube and `kubectl`               | The Kubernetes path only                                                                                                      |

**Expect the first build to be slow.** On the author's Windows laptop the first `docker compose up --build` took about **51 minutes**. Later starts take seconds.

## Run with Docker Compose

```bash
# 1. Configuration. Compose reads .env automatically.
cp .env.example .env          # if present; otherwise create .env yourself
#    set JWT_SECRET to a long random value; without it Compose uses the
#    insecure placeholder "change-me-in-production"

# 2. Build and start everything (gateway, doc-ocr, asr, postgres, redis)
docker compose up --build -d

# 3. Wait for readiness (Postgres and Redis reachable)
curl -s http://localhost:8000/ready        # {"status":"ready"}
```

| Task                           | Command                                                                                       |
| ------------------------------ | --------------------------------------------------------------------------------------------- |
| Start again later              | `docker compose up -d`                                                                        |
| Stop, keep data                | `docker compose stop` / `docker compose start`                                                |
| Remove containers, keep data   | `docker compose down`                                                                         |
| Remove containers **and data** | `docker compose down -v` (deletes the Postgres and Redis volumes; do not do this by accident) |
| Logs                           | `docker compose logs -f gateway`                                                              |

What starts:

| Service             | Host ports                |
| ------------------- | ------------------------- |
| `gateway`           | 8000                      |
| `doc-ocr-worker`    | 8001 (HTTP), 50051 (gRPC) |
| `asr-worker`        | 8002 (HTTP), 50052 (gRPC) |
| `postgres`, `redis` | not published             |

The worker ports are published for convenience and are **unauthenticated**; see [Security](security.md). `db/init.sql` runs only when the Postgres volume is **empty**; if you had an older volume, apply `migrations/*.sql` by hand ([Data stores](data-stores.md#when-the-schema-is-applied)). The ClickHouse metrics store is not part of Compose, so metrics are off.

## Create sample files

The smoke test and load test expect two files in `fixtures/`, which is git-ignored and not shipped. This creates them (needs Pillow 10.1 or newer, tested with 10.3):

```bash
mkdir -p fixtures
python - <<'EOF'
from PIL import Image, ImageDraw, ImageFont
img = Image.new("RGB", (900, 200), "white")
ImageDraw.Draw(img).text((30, 60), "HELLO MODELMESH 12345", fill="black",
                         font=ImageFont.load_default(size=56))
img.save("fixtures/sample_document.jpg")
import wave
with wave.open("fixtures/sample_audio.wav", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"\x00\x00" * 16000 * 2)      # 2 s of silence
EOF
```

The audio is silence, so ASR returns empty text: it tests the plumbing, not transcription quality. Use a short real speech `.wav` if you want to see a transcript.

## Your first requests

```bash
BASE=http://localhost:8000

# Register and log in
curl -s -X POST $BASE/v1/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"a-long-password"}'
JWT=$(curl -s -X POST $BASE/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"a-long-password"}' | jq -r .access_token)

# Create an API key (shown once; keep it)
API_KEY=$(curl -s -X POST $BASE/v1/auth/api-keys -H "Authorization: Bearer $JWT" \
  -H 'Content-Type: application/json' -d '{"rate_limit_per_min":100}' | jq -r .key)

# See the models (no auth needed)
curl -s $BASE/v1/models | jq

# Submit a document, then poll the job
JOB=$(curl -s -X POST $BASE/v1/infer/doc-ocr -H "Authorization: Bearer $API_KEY" \
  -F "file=@fixtures/sample_document.jpg" | jq -r .job_id)
sleep 3
curl -s $BASE/v1/jobs/$JOB -H "Authorization: Bearer $API_KEY" | jq
```

Expected shape of the finished job: `{"job_id": "…", "status": "success", "result": {"text": "HELLO MODELMESH 12345", "avg_confidence": …, "line_count": 1}}` (the exact text depends on OCR). Full contract: [API](api.md).

### The same over a WebSocket

```bash
pip install websockets
python test_ws_client.py "$API_KEY" asr fixtures/sample_audio.wav
python test_ws_client.py "$API_KEY" doc-ocr fixtures/sample_document.jpg
```

The client prints each message and `FINAL_RESULT=yes` on success, and exits non-zero on any error or timeout. It connects to `ws://localhost:8000` (change the script for another host).

## Becoming an admin

Registering, updating or disabling models needs an admin API key. There is **no API** to create one; promote a key in the database. With Compose:

```bash
docker compose exec -T postgres psql -U user -d modelmesh -c \
  "UPDATE api_keys SET is_admin = TRUE WHERE key_hash = encode(digest('$API_KEY','sha256'),'hex');"
```

Then `Authorization: Bearer $API_KEY` works on `POST/PATCH/DELETE /v1/models`. Keep admin keys out of shell history, shared logs and command-line arguments.

## Smoke test

```bash
./scripts/smoke_test.sh                 # BASE defaults to http://localhost:8000
./scripts/smoke_test.sh --latency 5     # adds a median/p95 section
```

It needs `curl`, `jq`, the fixtures, and Python with `websockets` (it looks for it in `$MODEL_MESH_PYTHON`, `$VIRTUAL_ENV`, `~/.venvs/modelmesh`, `.venv/modelmesh-env`, then `python3`). It checks health and readiness, the model list, auth, an OCR job (the text must contain `HELLO`), an ASR job, both WebSocket paths, the rate limit (a key with limit 2 must give 200, 200, 429), an admin `PATCH` and restore, and prints how long a bad upload takes to fail. The last line is `SMOKE: PASS` or `SMOKE: FAIL (n checks failed)`.

Caveats: the admin check uses `docker compose exec`, so it works **only against a Compose stack**; and with `--latency` it polls every 2 seconds, so recorded times are quantised to about 2-second steps (see [Testing](testing.md#the-other-scripts)).

## Run on Kubernetes (minikube)

These manifests are written for a local minikube. They have no Ingress, TLS, autoscaling or persistent Redis, and they use development secrets.

```bash
minikube start --cpus=2 --memory=4096
minikube addons enable metrics-server

# Build the three images inside minikube's Docker
eval $(minikube docker-env)
docker build -t modelmesh-gateway:latest -f gateway/Dockerfile .
docker build -t modelmesh-doc-ocr:latest -f workers/doc_ocr/Dockerfile .
docker build -t modelmesh-asr:latest     -f workers/asr/Dockerfile .

# Apply, in this order
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml                 # DB credentials: replace the dev values first
kubectl apply -f k8s/redis/
kubectl apply -f k8s/postgres/
kubectl apply -f k8s/gateway/                     # includes k8s/gateway/jwt-secret.yaml
kubectl apply -f k8s/clickhouse/
kubectl wait --for=condition=available deployment/clickhouse -n modelmesh --timeout=180s
kubectl exec -i -n modelmesh deployment/clickhouse -- clickhouse-client --multiquery < clickhouse/init.sql
kubectl apply -f k8s/workers/

kubectl get pods -n modelmesh
kubectl port-forward -n modelmesh svc/gateway 8000:8000
```

Notes:

- `k8s/secrets.yaml` and `k8s/gateway/jwt-secret.yaml` are referred to by the setup instructions but were **not in the reviewed export**. Check that they exist and that you have replaced the development values.
- The images use `imagePullPolicy: Never`, so `eval $(minikube docker-env)` must be run **before** building.
- Minikube was started with `--memory=4096` in the instructions but reported about 7.58 GiB allocatable, so that figure is not a verified 4 GiB limit. The OCR pod needed a 3 GiB limit after 1.5 GiB and 2 GiB were each OOM-killed at start-up.
- The Postgres PVC is 1 Gi on minikube's host path. Redis and ClickHouse have **no** volume.
- The first worker pods take a while: readiness probes begin after 30 s, and model loading is slow on 2 CPUs.
- Admin promotion on Kubernetes uses `kubectl exec` into the Postgres pod with the same SQL as above.

Scale a worker with `kubectl scale deployment doc-ocr-worker -n modelmesh --replicas=3`; replicas share the consumer group.

## Run the tests

See [Testing](testing.md#running-the-tests). In short: a scratch Postgres database and Redis (a Compose overlay publishes them), `pip install -r tests/requirements.txt`, set `TEST_DATABASE_URL` and `TEST_REDIS_URL`, run `pytest -q`.

## Load test

`load-test.yml` is an Artillery scenario (5, 20 then 100 requests per second against OCR, ASR and `GET /v1/models`). It reads API keys from `load-test-vars.csv`, which is git-ignored and was not exported; create it with a header `apiKey` and one key per row. With the default rate limit of 60 per minute per key, a run at these rates will mostly measure `429` responses unless the keys have high limits. The only recorded baseline measured gateway **enqueue** latency (p50 2 ms, p95 4 ms) for accepted requests, not end-to-end inference.
