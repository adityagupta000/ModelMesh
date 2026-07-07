# Phase 4 — Kubernetes Deployment

## Implementation Status: COMPLETE ✓

**Goal:** Move entire stack from Docker Compose to Kubernetes orchestration. Production-ready manifests for local (minikube) deployment.

**Actual time:** Completed

---

## What Was Implemented

### Core Features (All Complete)

- Namespace isolation (`modelmesh`)
- StatefulSet for Postgres with PersistentVolume
- Deployments for Redis, Gateway, Workers
- Services for internal communication
- ConfigMap for Postgres init SQL
- Secrets for JWT + DB passwords
- Multi-service containers in workers (HTTP + gRPC + Redis consumer)
- Resource requests and limits
- Readiness and liveness probes
- Protobuf compilation in entrypoint scripts

### Manifest Structure

```
k8s/
├── namespace.yaml              # modelmesh namespace
├── secrets.yaml                # JWT secret, DB passwords
├── redis/
│   ├── deployment.yaml
│   └── service.yaml
├── postgres/
│   ├── statefulset.yaml        # With PVC
│   ├── service.yaml
│   └── init-configmap.yaml     # init.sql schema
├── gateway/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── jwt-secret.yaml
├── workers/
│   ├── doc-ocr-deployment.yaml
│   ├── doc-ocr-service.yaml
│   ├── asr-deployment.yaml
│   └── asr-service.yaml
└── clickhouse/                 # Phase 5
    ├── deployment.yaml
    └── service.yaml
```

---

## Definition of Done

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

## Environment Constraints

**Minikube setup**: Used `--cpus=2 --memory=4096` instead of planned 8192MB due to WSL2 host RAM limits (7.6GB total). Required stopping Docker Compose stack first to free memory.

**Image building**: Must run `eval $(minikube docker-env)` before building images in each new terminal session, otherwise images build to host Docker daemon and pods fail to find them.

---

## Quick Start

```bash
# 1. Start minikube
minikube start --cpus=2 --memory=4096
minikube addons enable metrics-server

# 2. Build images in minikube's Docker
eval $(minikube docker-env)
docker build -t modelmesh-gateway:latest -f gateway/Dockerfile .
docker build -t modelmesh-doc-ocr:latest -f workers/doc_ocr/Dockerfile .
docker build -t modelmesh-asr:latest -f workers/asr/Dockerfile .

# 3. Deploy to Kubernetes
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/redis/
kubectl apply -f k8s/postgres/
kubectl apply -f k8s/gateway/
kubectl apply -f k8s/workers/

# 4. Check status
kubectl get pods -n modelmesh
kubectl get svc -n modelmesh

# 5. Access gateway
kubectl port-forward -n modelmesh svc/gateway 8000:80
curl http://localhost:8000/v1/models
```

---

## Key Design Decisions

**StatefulSet for Postgres**: Stable network identity (postgres-0) + persistent volume survives pod restarts.

**Deployments for workers**: Stateless (job queue handles state), can scale horizontally, rolling updates safe.

**Multi-service containers**: Workers run HTTP (8001) + gRPC (50051) + Redis consumer concurrently. Entrypoint script backgrounds HTTP and gRPC, runs consumer in foreground.

**Resource limits**: All services have requests (minimum guaranteed) and limits (maximum allowed) to prevent resource starvation.

**Secrets management**: Current secrets are committed (NOT for production). Production should use external secret management (AWS Secrets Manager, Vault, etc.).

---

## Scaling Workers

```bash
# Scale doc-ocr to 3 replicas
kubectl scale deployment doc-ocr-worker -n modelmesh --replicas=3

# Scale ASR to 2 replicas
kubectl scale deployment asr-worker -n modelmesh --replicas=2

# Jobs automatically distributed via Redis Streams consumer groups
```

---

## Monitoring

```bash
# Pod status
kubectl get pods -n modelmesh -w

# Pod logs
kubectl logs -n modelmesh -f deployment/gateway
kubectl logs -n modelmesh -f deployment/doc-ocr-worker

# Resource usage (requires metrics-server)
kubectl top pods -n modelmesh
kubectl top nodes
```

---

## Health Checks

**Readiness probes**: Is service ready to accept traffic?

- Gateway: HTTP GET /v1/models
- Workers: HTTP GET /health

**Liveness probes**: Is service alive?

- Gateway: HTTP GET /v1/models
- Workers: HTTP GET /health

Failed probes trigger Kubernetes pod restarts.

---

## Persistent Storage

**Postgres**: Uses PersistentVolumeClaim (1Gi)

- minikube: hostPath provisioner (data survives pod restarts, not cluster deletion)
- Production: Use cloud storage classes (EBS, pd-ssd, managed-premium)

---

## Implementation Notes

**Postgres init**: Originally planned to seed schema at container startup. Implemented via ConfigMap mounting `init.sql` into `/docker-entrypoint-initdb.d/`.

**gRPC ports**: Exposed via Services:

- doc-ocr-worker: 50051
- asr-worker: 50052 (mapped from internal 50051 to avoid conflict)

**Protobuf compilation**: Stubs generated at container startup via entrypoint script, not at build time. Enables iterative development.

---

## Next Steps (Production Readiness)

Not implemented in Phase 4, but would be needed for production:

- Ingress Controller (nginx/traefik) for external access
- TLS certificates (cert-manager + Let's Encrypt)
- Horizontal Pod Autoscaler (HPA)
- NetworkPolicy for pod-to-pod rules
- External secret management
- Monitoring stack (Prometheus + Grafana) → Phase 5
- CI/CD pipeline (ArgoCD/FluxCD)

---

## Next Phase

Phase 5: Observability + Canary Deployment (ClickHouse, Prometheus, Grafana, canary rollout)
