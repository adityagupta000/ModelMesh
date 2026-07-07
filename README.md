# ModelMesh — Multi-Model Inference Gateway

A multi-model inference gateway with dynamic model registry, async job queues, real-time streaming, and Kubernetes deployment.

**Status**: Phases 1-4 Complete ║ Phase 5 Partial (ClickHouse ✓, Canary ✓, Prom/Graf ✗)

---

## Overview

ModelMesh is a horizontally scalable inference gateway designed for multi-model serving workloads. Built from the ground up with a **registry-driven architecture**, it eliminates hardcoded model dependencies and enables zero-downtime deployments, protocol switching (HTTP ↔ gRPC), and traffic-based canary rollouts—all via API calls, not code changes.

### Key Features

- **Dynamic Model Registry**: Register, version, and route models via database lookups
- **Async Job Queue**: Redis Streams with consumer groups + Kafka alternative
- **Real-Time Streaming**: WebSocket endpoints with bidirectional gRPC backend
- **Kubernetes Native**: StatefulSets, PVCs, multi-service containers, autoscaling-ready
- **Observability**: ClickHouse analytics, Prometheus metrics instrumentation, canary routing logic
- **Core Features**: JWT auth, API key management, rate limiting, retry logic, DLQ

### Tech Stack

- **Gateway**: FastAPI (Python 3.11+)
- **Workers**: EasyOCR, Whisper (PyTorch-based models)
- **Queue**: Redis Streams (default), Kafka (optional)
- **Storage**: PostgreSQL (transactional), ClickHouse (analytical)
- **Protocols**: HTTP/REST, WebSocket, gRPC/Protobuf
- **Orchestration**: Docker Compose (dev), Kubernetes (prod)

---

## Quick Start

### Prerequisites

**WSL2 Ubuntu required** (Windows users). Model caches split across filesystems if run on Windows Python.

#### 1. Install Models

```bash
pip install easyocr openai-whisper
sudo apt install ffmpeg -y  # Required for Whisper audio decoding
```

#### 2. Verify Downloads

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

### Docker Compose (Development)

```bash
cp .env.example .env  # Set JWT_SECRET
docker compose up --build
```

Gateway available at `http://localhost:8000` | Docs: `http://localhost:8000/docs`

### Kubernetes (minikube)

See [Phase 4 Documentation](docs/Phase4-Kubernetes-Deployment.md) for local minikube setup and manifest deployment.

---

## Usage

### 1. Authentication

```bash
# Register user
curl -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secure_pass"}'

# Login (returns JWT)
JWT=$(curl -X POST http://localhost:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secure_pass"}' \
  | jq -r .access_token)

# Issue API key
API_KEY=$(curl -X POST http://localhost:8000/v1/auth/api-keys \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"rate_limit_per_min": 100}' \
  | jq -r .key)
```

### 2. Inference (Async)

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

### 3. Streaming (WebSocket)

```bash
# Python CLI
python test_ws_client.py $API_KEY asr audio.wav

# Browser: Open test_ws_client.html
```

### 4. Model Registry

```bash
# List models
curl http://localhost:8000/v1/models

# Register new model (admin only)
curl -X POST http://localhost:8000/v1/models \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "custom-nlp",
    "version": "v1",
    "worker_name": "nlp-worker",
    "protocol": "grpc",
    "endpoint": "nlp-worker:50051",
    "description": "Custom NLP model"
  }'

# Update protocol (HTTP ↔ gRPC switch)
curl -X PATCH http://localhost:8000/v1/models/<model-id> \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -H "Content-Type: application/json" \
  -d '{"protocol": "grpc", "endpoint": "doc-ocr-worker:50051"}'
```

**Zero gateway code changes.** All routing resolved via database.

---

## Architecture

### Phase 1: Core Gateway

```
Client → Gateway (FastAPI) → Worker (HTTP)
         ↓
         PostgreSQL (users, keys, models, requests)
         Redis (rate limit, cache)
```

[Documentation](docs/Phase1-Core-Gateway.md)

### Phase 2: Async Job Queue

```
Client → Gateway → Redis Streams / Kafka → Workers
         ↓                                    ↓
         Returns job_id                       Process async
                                              ↓
Client polls /v1/jobs/{id} ← Redis result cache
```

[Documentation](docs/Phase2-Async-Job-Queue.md)

### Phase 3: Streaming + gRPC

```
Client → Gateway (WebSocket) → Worker (gRPC stream) → Partial results
         Real-time push           Binary protocol

Client → Gateway (HTTP) → Worker (gRPC unary) → Result
```

[Documentation](docs/Phase3-Streaming-gRPC-WebSockets.md)

### Phase 4: Kubernetes

```
Namespace: modelmesh
  ├── Gateway (Deployment, replicas=2)
  ├── Workers (Deployment, autoscaling)
  ├── Postgres (StatefulSet, PVC)
  ├── Redis (Deployment)
  └── Services (ClusterIP, internal DNS)
```

[Documentation](docs/Phase4-Kubernetes-Deployment.md)

### Phase 5: Observability + Canary

```
Request → Gateway → ClickHouse (analytics)
                 → Prometheus (metrics)
                 → Canary routing (v1 90%, v2 10%)
```

[Documentation](docs/Phase5-Observability-Canary.md)

---

## Key Design Decisions

### Registry-Driven Routing

**Problem**: Hardcoded model names in gateway force code deploys for new models.

**Solution**: All routing resolved via `models` table lookup. Adding a model = `POST /v1/models`, not a code change.

**Payoff**:

- Phase 3: HTTP → gRPC switch is a `PATCH` request, not a deployment
- Phase 5: Canary rollout is a database row insert, not K8s selector manipulation

### Async Queue Architecture

**Trade-off**: Clients poll `/v1/jobs/{id}` (Phase 2) vs instant response (Phase 1).

**Gain**: Gateway never blocks. Workers scale independently. Automatic retries. No lost work on crashes.

**Mitigation**: Phase 3 adds WebSockets to eliminate polling while keeping queue benefits.

### Multi-Service Containers

Workers run **3 services concurrently**:

1. HTTP server (port 8001) — Phase 1 legacy
2. gRPC server (port 50051) — Phase 3
3. Redis consumer (foreground) — Phase 2

**Why not separate pods?** Shared model weights in memory. Splitting = 3x memory footprint.

**How?** Entrypoint script backgrounds HTTP/gRPC, runs consumer in foreground. Container death = consumer crash (intentional).

---

## Testing

### Unit + Integration Tests

```bash
docker compose up postgres redis -d
psql postgresql://user:pass@localhost/postgres -c "CREATE DATABASE modelmesh_test;"
psql postgresql://user:pass@localhost/modelmesh_test < db/init.sql

pip install -r tests/requirements.txt
pytest
```

### Load Testing (Artillery)

```bash
npm install -g artillery

# Create load-test-vars.csv with valid API key
echo "apiKey" > load-test-vars.csv
echo "$API_KEY" >> load-test-vars.csv

artillery run load-test.yml
```

Captures p50/p95/p99 latency across phases for performance regression tracking.

---

## Deployment

### Docker Compose (Local Development)

```bash
docker compose up --build
```

Includes: Gateway, 2 workers, Postgres, Redis. Hot-reload enabled on gateway.

### Kubernetes (Production)

#### Minikube (Local)

```bash
minikube start --cpus=2 --memory=4096
minikube addons enable metrics-server

eval $(minikube docker-env)
docker build -t modelmesh-gateway:latest -f gateway/Dockerfile .
docker build -t modelmesh-doc-ocr:latest -f workers/doc_ocr/Dockerfile .
docker build -t modelmesh-asr:latest -f workers/asr/Dockerfile .

kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/redis/
kubectl apply -f k8s/postgres/4
kubectl apply -f k8s/gateway/
kubectl apply -f k8s/workers/

kubectl port-forward -n modelmesh svc/gateway 8000:80
```

#### Cloud (GKE/EKS/AKS)

1. Replace `imagePullPolicy: Never` with `Always` in deployments
2. Push images to container registry (GCR/ECR/ACR)
3. Replace `secrets.yaml` with external secret manager
4. Add Ingress + TLS via cert-manager
5. Enable HPA (Horizontal Pod Autoscaler)

See [Production Readiness](docs/Phase4-Kubernetes-Deployment.md#next-steps-production-readiness) for complete checklist.

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

### ClickHouse Analytics (Phase 5)

```sql
SELECT model_name, model_version,
       count() as requests,
       avg(latency_ms),
       quantile(0.95)(latency_ms) as p95
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR
GROUP BY model_name, model_version;
```

---

## Phase Implementation Status

| Phase       | Status        | Features                                                                                          | Documentation                                                                   |
| ----------- | ------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| **Phase 1** | Complete      | Core gateway, registry, auth                                                                      | [Phase1-Core-Gateway.md](docs/Phase1-Core-Gateway.md)                           |
| **Phase 2** | Complete      | Async queue, retry, DLQ                                                                           | [Phase2-Async-Job-Queue.md](docs/Phase2-Async-Job-Queue.md)                     |
| **Phase 3** | Complete      | WebSocket, gRPC, streaming                                                                        | [Phase3-Streaming-gRPC-WebSockets.md](docs/Phase3-Streaming-gRPC-WebSockets.md) |
| **Phase 4** | Complete      | Kubernetes, StatefulSets                                                                          | [Phase4-Kubernetes-Deployment.md](docs/Phase4-Kubernetes-Deployment.md)         |
| **Phase 5** | Partial (70%) | ClickHouse , Canary , Metrics instrumentation <br>Prom/Graf deployment [X] (resource constraints) | [Phase5-Observability-Canary.md](docs/Phase5-Observability-Canary.md)           |

### Phase 5 Implementation Details

**What's Complete:**

- ClickHouse Kubernetes deployment + service
- `request_metrics` table with columnar storage (MergeTree engine)
- Fire-and-forget metric logging (never blocks inference)
- Canary routing logic (`canary_percent` field, registry-driven traffic splitting)
- Version pinning support (`?version=v1` bypasses canary)
- Prometheus metrics instrumentation (Counter, Histogram with model_name/model_version labels)
- `/metrics` endpoints exposed on all services
- Verified via ClickHouse SQL queries and canary split testing

**What's Not Deployed (and Why):**

- **Prometheus + Grafana pods**: Intentionally scoped out due to minikube resource constraints
  - **Environment**: WSL2 Ubuntu with 7.6GB host RAM
  - **Minikube allocation**: 2 CPU / 4GB RAM (had to stop Docker Compose to free memory)
  - **Current stack**: ClickHouse + Gateway + 2 Workers + Postgres + Redis ≈ 3.8GB
  - **Adding Prom/Graf**: Would require additional ~1-1.5GB, exceeding available memory
  - **Verification approach**: Direct ClickHouse queries prove metrics pipeline works
  - **Production note**: All instrumentation code is in place. Deploy Prom/Graf with `helm install` in environments with adequate resources—no code changes needed.

---

## Project Structure

```
modelmesh/
├── gateway/
│   ├── main.py              # FastAPI app, routes
│   ├── auth.py              # JWT + API key verification
│   ├── registry.py          # Model registry service
│   ├── job_queue.py         # Redis Streams queue
│   ├── grpc_client.py       # gRPC client
│   ├── metrics.py           # ClickHouse logging
│   └── requirements.txt
├── workers/
│   ├── doc_ocr/
│   │   ├── inference.py     # EasyOCR wrapper
│   │   ├── consumer.py      # Redis consumer
│   │   ├── grpc_server.py   # gRPC server
│   │   └── entrypoint.sh    # Multi-service startup
│   └── asr/
│       ├── inference.py     # Whisper wrapper
│       ├── consumer.py      # Redis consumer
│       ├── grpc_server.py   # gRPC server (streaming)
│       └── entrypoint.sh
├── k8s/                     # Kubernetes manifests
│   ├── namespace.yaml
│   ├── secrets.yaml
│   ├── gateway/
│   ├── workers/
│   ├── postgres/
│   ├── redis/
│   └── clickhouse/
├── db/
│   └── init.sql             # Database schema
├── docs/                    # Phase documentation
│   ├── Phase1-Core-Gateway.md
│   ├── Phase2-Async-Job-Queue.md
│   ├── Phase3-Streaming-gRPC-WebSockets.md
│   ├── Phase4-Kubernetes-Deployment.md
│   └── Phase5-Observability-Canary.md
├── tests/
│   ├── test_auth.py
│   ├── test_registry.py
│   └── test_workers.py
├── inference.proto          # gRPC contract
├── docker-compose.yml
└── README.md
```

---

## Limitations & Scope

**What this is:**
- Development/learning project demonstrating ML serving architecture
- Tested on Docker Compose and local minikube only
- Built to showcase registry-driven design patterns

**What this is NOT:**
- Production-ready (secrets committed to git, no TLS, no external monitoring deployment)
- Tested at scale (minikube: 2 CPU / 4GB RAM)
- Multi-tenant ready (basic auth, no isolation)

**Known gaps:**
- Kubernetes secrets committed to repo (NOT production-safe)
- Prometheus + Grafana instrumented but not deployed (resource constraints)
- No Ingress controller or TLS
- No CI/CD pipeline
- Tested with small payloads only
