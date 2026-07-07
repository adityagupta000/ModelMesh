# ModelMesh — Multi-Model Inference Gateway

A production-ready model serving platform with async job queues, dynamic model registry, and comprehensive observability.

**Current Status**: Phase 4 Complete (Kubernetes Deployment)

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

### Phase 3

- [x] WebSocket endpoint `/v1/ws/infer/{model_name}` working
- [x] API key authentication via query parameter
- [x] gRPC servers in all workers (HTTP + gRPC + Redis consumer)
- [x] Protocol abstraction in registry (http vs grpc)
- [x] Protobuf contract for InferenceWorker service
- [x] Bidirectional streaming RPC (PredictStream)
- [x] Unary RPC (Predict)
- [x] WebSocket test clients (Python + HTML)
- [x] Zero code changes to switch protocols

### Phase 4

- [x] Kubernetes namespace (modelmesh)
- [x] Redis Deployment + Service
- [x] Postgres StatefulSet + Service + PVC
- [x] Postgres init ConfigMap with schema
- [x] Gateway Deployment + Service
- [x] Worker Deployments + Services (doc-ocr, asr)
- [x] Secrets for JWT and DB passwords
- [x] Resource requests and limits on all pods
- [x] Readiness and liveness probes
- [x] Multi-service containers in workers
- [x] Protobuf compilation in entrypoint
- [x] Verified on minikube with port-forward access

---

## Phase 3: WebSockets + gRPC (Complete)

Real-time streaming for clients via WebSockets and high-performance internal communication via gRPC.

### Features Implemented

- **WebSocket endpoint** (`/v1/ws/infer/{model_name}`) for real-time streaming
- **gRPC workers** with Protocol Buffers for internal communication
- **Bidirectional streaming** for ASR (PredictStream RPC)
- **Unary gRPC** for doc-ocr (Predict RPC)
- **Protocol abstraction** in registry (http vs grpc switchable)
- **Multi-service containers** (HTTP server + gRPC server + Redis consumer)
- WebSocket test clients (Python CLI + browser HTML)
- Protobuf compilation in Docker build process

### Architecture

```
Client → Gateway (WebSocket) → Worker (gRPC stream) → Partial results
         Real-time push

Client → Gateway (HTTP) → Worker (gRPC unary) → Result
         Sync path with gRPC
```

### WebSocket Usage

#### Python CLI

```bash
python test_ws_client.py <api_key> asr audio.wav
python test_ws_client.py <api_key> doc-ocr image.jpg
```

#### Browser

Open `test_ws_client.html`, enter API key, select file, click "Connect & Send"

#### WebSocket Protocol

1. Client connects: `ws://localhost:8000/v1/ws/infer/{model_name}?api_key=xxx`
2. Server sends: `{"status": "ready"}`
3. Client sends file bytes
4. Server streams results: `{"text": "...", "is_final": false}`
5. Final result: `{"text": "...", "is_final": true}`
6. Connection closes

### gRPC Configuration

Models can switch between HTTP and gRPC via registry:

```sql
-- Switch doc-ocr to gRPC
UPDATE models
SET protocol = 'grpc', endpoint = 'doc-ocr-worker:50051'
WHERE name = 'doc-ocr';

-- Switch back to HTTP
UPDATE models
SET protocol = 'http', endpoint = 'http://doc-ocr-worker:8001/infer'
WHERE name = 'doc-ocr';
```

Zero gateway code changes required.

### gRPC Ports

- doc-ocr-worker: 50051
- asr-worker: 50052

### Key Design Decisions

**Why WebSockets?**

- Eliminates polling overhead from Phase 2
- Server pushes updates as they arrive
- Bi-directional: client streams audio chunks, receives transcription chunks
- Lower latency for real-time use cases

**Why gRPC?**

- Binary protocol (faster than JSON)
- Strongly-typed contracts (protobuf)
- Built-in bidirectional streaming
- Lower latency for high-frequency calls

**Trade-off**: More complexity (protobuf compilation, WebSocket lifecycle management)

### Streaming Behavior

**ASR streaming caveat**: Whisper doesn't support token-level streaming. Current implementation:

1. Client sends full audio over WebSocket
2. Worker processes entire file (Whisper limitation)
3. Worker sends single result as "partial"
4. Worker marks `is_final: true`

This is chunk-level streaming, not token-by-token. Infrastructure is in place for future models with true incremental output.

---

## Phase 4: Kubernetes Deployment (Complete)

Production-ready Kubernetes manifests for local (minikube) and cloud deployment.

### Features Implemented

- **Namespace** isolation (modelmesh)
- **StatefulSet** for Postgres with persistent volume
- **Deployments** for Redis, Gateway, Workers
- **Services** for internal communication
- **ConfigMaps** for Postgres init SQL
- **Secrets** for sensitive data (JWT, DB passwords)
- **Multi-service containers** in workers (HTTP + gRPC + Redis consumer)
- Resource requests and limits
- Health checks (readiness + liveness probes)

### Quick Start (minikube)

#### 1. Start minikube

```bash
minikube start --cpus=2 --memory=4096
minikube addons enable metrics-server
```

#### 2. Build images in minikube's Docker

```bash
eval $(minikube docker-env)
docker build -t modelmesh-gateway:latest -f gateway/Dockerfile .
docker build -t modelmesh-doc-ocr:latest -f workers/doc_ocr/Dockerfile .
docker build -t modelmesh-asr:latest -f workers/asr/Dockerfile .
```

#### 3. Deploy to Kubernetes

```bash
# Apply all manifests
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/redis/
kubectl apply -f k8s/postgres/
kubectl apply -f k8s/gateway/
kubectl apply -f k8s/workers/

# Check status
kubectl get pods -n modelmesh
kubectl get svc -n modelmesh
```

#### 4. Access the gateway

```bash
# Port-forward gateway
kubectl port-forward -n modelmesh svc/gateway 8000:80

# Gateway now at http://localhost:8000
curl http://localhost:8000/v1/models
```

### Manifest Structure

```
k8s/
├── namespace.yaml              # modelmesh namespace
├── secrets.yaml                # JWT secret, DB passwords
├── redis/
│   ├── deployment.yaml         # Redis single replica
│   └── service.yaml
├── postgres/
│   ├── statefulset.yaml        # Postgres with PVC
│   ├── service.yaml
│   └── init-configmap.yaml     # init.sql schema
├── gateway/
│   ├── deployment.yaml         # Gateway replicas
│   ├── service.yaml
│   └── jwt-secret.yaml         # JWT secret (separate)
└── workers/
    ├── doc-ocr-deployment.yaml
    ├── doc-ocr-service.yaml
    ├── asr-deployment.yaml
    └── asr-service.yaml
```

### Key Design Decisions

**Why StatefulSet for Postgres?**

- Stable network identity (postgres-0)
- Persistent volume survives pod restarts
- Ordered deployment and scaling

**Why Deployments for workers?**

- Stateless (job queue handles state)
- Can scale horizontally
- Rolling updates without data loss

**Resource Limits**

All services have:

- **Requests**: Minimum guaranteed resources
- **Limits**: Maximum allowed resources
- Prevents resource starvation

Example:

```yaml
resources:
  requests:
    memory: "256Mi"
    cpu: "250m"
  limits:
    memory: "512Mi"
    cpu: "500m"
```

### Scaling Workers

```bash
# Scale doc-ocr to 3 replicas
kubectl scale deployment doc-ocr-worker -n modelmesh --replicas=3

# Scale ASR to 2 replicas
kubectl scale deployment asr-worker -n modelmesh --replicas=2

# Jobs automatically distributed via Redis Streams consumer groups
```

### Monitoring

```bash
# Pod status
kubectl get pods -n modelmesh -w

# Pod logs
kubectl logs -n modelmesh -f deployment/gateway
kubectl logs -n modelmesh -f deployment/doc-ocr-worker
kubectl logs -n modelmesh -f deployment/asr-worker

# Describe pod (events, resource usage)
kubectl describe pod -n modelmesh <pod-name>

# Resource usage (requires metrics-server)
kubectl top pods -n modelmesh
kubectl top nodes
```

### Secrets Management

**Local development** (committed to git, NOT for production):

```yaml
# k8s/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: modelmesh-secrets
  namespace: modelmesh
stringData:
  jwt-secret: "dev-secret-change-in-prod"
  db-password: "password"
```

**Production** (use external secret management):

- AWS Secrets Manager → External Secrets Operator
- HashiCorp Vault → Vault CSI driver
- Google Secret Manager → GKE Workload Identity

Never commit production secrets to git.

### Health Checks

All services have readiness and liveness probes:

**Readiness**: Is the service ready to accept traffic?

- Gateway: HTTP GET /v1/models
- Workers: HTTP GET /health

**Liveness**: Is the service alive?

- Gateway: HTTP GET /v1/models
- Workers: HTTP GET /health

Failed probes → Kubernetes restarts the pod.

### Persistent Storage

**Postgres** uses PersistentVolumeClaim (PVC):

```yaml
volumeClaimTemplates:
  - metadata:
      name: postgres-data
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 1Gi
```

**minikube**: Uses hostPath provisioner (data survives pod restarts, not cluster deletion)

**Production**: Use cloud storage classes:

- AWS: gp3 EBS volumes
- GCP: pd-ssd persistent disks
- Azure: managed-premium disks

### Next Steps (Production Readiness)

1. **Ingress Controller** (nginx/traefik) for external access
2. **TLS certificates** (cert-manager + Let's Encrypt)
3. **Horizontal Pod Autoscaler** (HPA) based on CPU/memory/custom metrics
4. **NetworkPolicy** for pod-to-pod communication rules
5. **External secret management** (AWS Secrets Manager, Vault)
6. **Monitoring stack** (Prometheus + Grafana) → Phase 5
7. **CI/CD pipeline** (GitOps with ArgoCD/FluxCD)

See `docs/Phase4-Kubernetes-Deployment.md` for full implementation notes.

---

## Definition of Done Checklist
