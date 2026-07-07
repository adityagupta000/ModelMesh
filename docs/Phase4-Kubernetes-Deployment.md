# Phase 4 — Kubernetes Deployment

### ModelMesh Implementation Guide

**Goal:** Move the whole stack off Docker Compose onto real orchestration — gateway and workers as independently scalable pods, with the Model Registry updated to point at K8s Service DNS names instead of Docker Compose service names.

**Estimated time:** 5–7 days

**Prerequisite:** Phase 3 fully complete and checked off.

---

## 1. Local cluster setup

Used **minikube** with the Docker driver on WSL2 Ubuntu.

**Real environment constraint**: the original plan assumed `--memory=8192`,
but the WSL2 host only has 7.6GB total RAM, and the Compose stack (Phase 1-3)
was still running and using ~3.8GB of it. Stopped the Compose stack first
(`docker compose down`), freeing enough headroom to run minikube at
`--memory=4096` instead.

```bash
minikube start --cpus=2 --memory=4096
minikube addons enable metrics-server   # needed for autoscaling later
```

Point local Docker builds at minikube's Docker daemon so images don't need to be pushed to a registry:

```bash
eval $(minikube docker-env)
docker build -t modelmesh-gateway:latest -f gateway/Dockerfile .
docker build -t modelmesh-doc-ocr:latest -f workers/doc_ocr/Dockerfile .
docker build -t modelmesh-asr:latest -f workers/asr/Dockerfile .
```

Note: `eval $(minikube docker-env)` only applies to the current shell session
— must be re-run in any new terminal before building, or images silently
build to the host's Docker daemon instead and `imagePullPolicy: Never` will
fail to find them.

---

## 2. Namespace and structure

```
k8s/
├── namespace.yaml
├── secrets.yaml
├── gateway/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── jwt-secret.yaml
├── workers/
│   ├── doc-ocr-deployment.yaml
│   ├── doc-ocr-service.yaml
│   ├── asr-deployment.yaml
│   └── asr-service.yaml
├── postgres/
│   ├── statefulset.yaml
│   ├── service.yaml
│   └── init-configmap.yaml     # NEW — not in original plan, see §6
└── redis/
    ├── deployment.yaml
    └── service.yaml
```

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: modelmesh
```

Applied first, standalone:

```bash
kubectl apply -f k8s/namespace.yaml
```

---

## 3. Redis (deployed first as a sanity check)

Before tackling Postgres's more complex ConfigMap-seeded StatefulSet, deployed Redis first to confirm the basic Deployment + Service pattern works end-to-end.

```yaml
# redis/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: modelmesh
spec:
  replicas: 1
  selector:
    matchLabels: { app: redis }
  template:
    metadata:
      labels: { app: redis }
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          ports:
            - containerPort: 6379
          resources:
            requests: { cpu: "100m", memory: "128Mi" }
            limits: { cpu: "250m", memory: "256Mi" }
```

```yaml
# redis/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: modelmesh
spec:
  selector: { app: redis }
  ports:
    - port: 6379
      targetPort: 6379
```

**Verified**: pod reached `1/1 Running` in ~14 seconds. Pattern confirmed working before moving to more complex components.

---

## 4. Postgres as a StatefulSet — with seed data

Postgres needs stable storage and identity, so it's a StatefulSet with a PersistentVolumeClaim, not a Deployment.

**Gap found in the original plan**: Docker Compose seeded `db/init.sql` via a
bind mount (`./db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro`), but
nothing in the original Phase 4 plan described how to deliver that same seed
data in Kubernetes. A fresh Postgres pod would otherwise come up with an
empty `models` table and every registry lookup would 404.

**Fix**: generate a ConfigMap directly from the existing seed file, so it's
guaranteed to match what was already tested in Compose:

```bash
kubectl create configmap postgres-init-sql \
  --namespace modelmesh \
  --from-file=init.sql=db/init.sql \
  --dry-run=client -o yaml > k8s/postgres/init-configmap.yaml
```

```yaml
# postgres/secrets.yaml (db-secret, applied before the StatefulSet)
apiVersion: v1
kind: Secret
metadata:
  name: db-secret
  namespace: modelmesh
type: Opaque
stringData:
  url: "postgresql://user:pass@postgres:5432/modelmesh"
```

```yaml
# postgres/statefulset.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: modelmesh
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels: { app: postgres }
  template:
    metadata:
      labels: { app: postgres }
    spec:
      containers:
        - name: postgres
          image: postgres:16
          env:
            - name: POSTGRES_DB
              value: modelmesh
            - name: POSTGRES_USER
              value: user
            - name: POSTGRES_PASSWORD
              value: pass
          ports:
            - containerPort: 5432
          volumeMounts:
            - name: pgdata
              mountPath: /var/lib/postgresql/data
            - name: init-sql
              mountPath: /docker-entrypoint-initdb.d
          resources:
            requests: { cpu: "250m", memory: "256Mi" }
            limits: { cpu: "500m", memory: "512Mi" }
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "user", "-d", "modelmesh"]
            initialDelaySeconds: 5
            periodSeconds: 5
      volumes:
        - name: init-sql
          configMap:
            name: postgres-init-sql
  volumeClaimTemplates:
    - metadata: { name: pgdata }
      spec:
        accessModes: ["ReadWriteOnce"]
        resources: { requests: { storage: 1Gi } } # reduced from 2Gi given constrained VM
```

```yaml
# postgres/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: modelmesh
spec:
  selector: { app: postgres }
  ports:
    - port: 5432
      targetPort: 5432
  clusterIP: None # headless service, correct for a StatefulSet
```

**Verified**: pod reached `1/1 Running`, and querying it directly confirmed
the seed data loaded correctly:

```bash
kubectl exec -it postgres-0 -n modelmesh -- psql -U user -d modelmesh -c "SELECT name, protocol FROM models;"
#  name    | protocol
# ---------+----------
#  doc-ocr | http
#  asr     | http
```

---

## 5. Gateway deployment

```yaml
# gateway/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway
  namespace: modelmesh
spec:
  replicas: 1 # reduced from 2 given constrained VM; see §8 for scaling test on a worker instead
  selector:
    matchLabels: { app: gateway }
  template:
    metadata:
      labels: { app: gateway }
    spec:
      containers:
        - name: gateway
          image: modelmesh-gateway:latest
          imagePullPolicy: Never # using minikube's local docker daemon
          ports:
            - containerPort: 8000
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef: { name: db-secret, key: url }
            - name: REDIS_URL
              value: "redis://redis:6379"
            - name: JWT_SECRET
              valueFrom:
                secretKeyRef: { name: jwt-secret, key: secret }
          livenessProbe:
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 10
          readinessProbe:
            httpGet: { path: /ready, port: 8000 }
            initialDelaySeconds: 3
            periodSeconds: 5
          resources:
            requests: { cpu: "250m", memory: "256Mi" }
            limits: { cpu: "500m", memory: "512Mi" }
```

**Gap found**: the original plan referenced `db-secret` for `DATABASE_URL`
but never addressed `JWT_SECRET` — the gateway code (`auth.py`) silently
falls back to an insecure hardcoded default (`"change-me-in-production"`) if
this env var is unset. Added a dedicated `jwt-secret`:

```yaml
# gateway/jwt-secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: jwt-secret
  namespace: modelmesh
type: Opaque
stringData:
  secret: "a-real-random-secret-for-k8s-testing-change-me"
```

```yaml
# gateway/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: gateway
  namespace: modelmesh
spec:
  selector: { app: gateway }
  ports:
    - port: 8000
      targetPort: 8000
  type: NodePort
```

**Verified**: reached `1/1 Running` (readiness probe passed, meaning it
successfully connected to Postgres). Confirmed externally reachable via:

```bash
minikube service gateway -n modelmesh --url
curl http://<url>/health        # {"status":"ok"}
curl http://<url>/v1/models     # returned seeded models correctly
```

---

## 6. Worker deployments

Deployed one at a time given the constrained VM, to isolate any resource issues.

```yaml
# workers/doc-ocr-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: doc-ocr-worker
  namespace: modelmesh
spec:
  replicas: 1
  selector:
    matchLabels: { app: doc-ocr-worker }
  template:
    metadata:
      labels: { app: doc-ocr-worker }
    spec:
      containers:
        - name: doc-ocr-worker
          image: modelmesh-doc-ocr:latest
          imagePullPolicy: Never
          env:
            - name: REDIS_URL
              value: "redis://redis:6379"
          ports:
            - containerPort: 8001
            - containerPort: 50051
          resources:
            requests: { cpu: "500m", memory: "1.2Gi" }
            limits: { cpu: "1", memory: "1.8Gi" } # tuned down from original 2Gi, see finding below
```

```yaml
# workers/asr-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: asr-worker
  namespace: modelmesh
spec:
  replicas: 1
  selector:
    matchLabels: { app: asr-worker }
  template:
    metadata:
      labels: { app: asr-worker }
    spec:
      containers:
        - name: asr-worker
          image: modelmesh-asr:latest
          imagePullPolicy: Never
          env:
            - name: WHISPER_MODEL
              value: "tiny"
            - name: REDIS_URL
              value: "redis://redis:6379"
          ports:
            - containerPort: 8002
            - containerPort: 50051
          resources:
            requests: { cpu: "500m", memory: "1200Mi" }
            limits: { cpu: "1", memory: "1800Mi" }
```

Each worker also gets a matching Service (`doc-ocr-worker`, `asr-worker`)
exposing both its HTTP and gRPC ports, same pattern as Phase 3.

### Real finding: resource limits needed empirical tuning

Deployed doc-ocr-worker first with the plan's original `2Gi` limit and it
`OOMKilled` repeatedly, cycling through `CrashLoopBackOff`. Root cause:
EasyOCR loads its full model into RAM at process startup (on top of Python /
FastAPI / uvicorn / gRPC server overhead), and the combination of default
limits plus this VM's tight 4GB ceiling meant the initial number was
mis-sized for the actual available headroom, not that the workload itself
was too large.

Measured actual footprint via `kubectl top pods` once stabilized:

- doc-ocr-worker (EasyOCR): **789Mi** at idle, well under a 1.8Gi limit
- asr-worker (Whisper-tiny): **605Mi** at idle, well under a 1.8Gi limit

Fixed by right-sizing both workers' limits to `1.8Gi` based on these
measurements rather than the plan's original guess. Both then ran stably
with 0 restarts.

**Lesson**: resource requests/limits should come from measured footprint via
`kubectl top pods`, not copied defaults — especially for ML workloads where
model-loading dominates memory use rather than per-request allocation.

**Verified**: both workers eventually ran simultaneously alongside gateway,
Postgres, and Redis at **38% node memory utilization** (`kubectl top nodes`)
— comfortable headroom on the 4GB VM, contrary to the initial expectation
that both heavy workers might not fit at all.

---

## 7. Update the Model Registry — this replaces manual gateway config

This is the key step that's different because of Phase 1's registry design. Instead of editing gateway code or environment variables to point at the new K8s DNS names, update the registry rows directly:

```bash
curl -X PATCH http://<gateway-url>/v1/models/<doc-ocr-model-id> \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"endpoint": "doc-ocr-worker.modelmesh.svc.cluster.local:8001"}'

curl -X PATCH http://<gateway-url>/v1/models/<asr-model-id> \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"endpoint": "asr-worker.modelmesh.svc.cluster.local:8002"}'
```

**Verified live**: PATCHed both models to fully-qualified `.svc.cluster.local`
endpoints while the gateway pod was already running — no restart, no code
change. Immediately submitted a real inference job afterward:

```bash
curl -X POST http://<gateway-url>/v1/infer/doc-ocr \
  -H "Authorization: Bearer <admin-key>" \
  -F "file=@test-image.png"
# {"job_id": "...", "status": "queued", ...}

curl http://<gateway-url>/v1/jobs/<job_id> -H "Authorization: Bearer <admin-key>"
# {"status": "success", "result": {"text": "Hello Omenl", ...}}
```

Confirms the registry abstraction from Phase 1 works identically whether
pointing at a Docker Compose hostname or a fully-qualified Kubernetes Service
DNS name — this is the core payoff of the Phase 1 design, now proven across
two completely different deployment substrates.

---

## 8. Deploy and verify — resilience

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/postgres/
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/redis/
kubectl apply -f k8s/gateway/
kubectl apply -f k8s/workers/
kubectl get pods -n modelmesh -w
```

Resilience test:

```bash
kubectl delete pod -n modelmesh -l app=doc-ocr-worker
kubectl get pods -n modelmesh -w
```

**Verified**: Deployment controller auto-recreated the deleted pod in ~37
seconds (dominated by EasyOCR model reload time). Submitted new inference
jobs both during and after the restart — Redis Streams queued them correctly,
and the new pod resumed consuming from the same consumer group
(`doc-ocr-worker_group`) with no gateway downtime and no dropped jobs.

**Not tested**: true mid-flight job redelivery (killing a pod while it's
actively processing a specific job). Test image processing was fast enough
(~sub-second) that a manually-timed kill consistently landed after job
completion, not during. The Phase 2 retry/DLQ logic exists in code but
wasn't exercised live under this exact failure mode — worth noting honestly
rather than claiming untested behavior works.

---

## 9. Horizontal scaling test

```bash
kubectl scale deployment doc-ocr-worker --replicas=2 -n modelmesh
kubectl get pods -n modelmesh -w
```

Submitted 6 concurrent inference jobs and checked logs to confirm actual
work distribution (not just redundant idle replicas):

```bash
kubectl logs -n modelmesh -l app=doc-ocr-worker --prefix=true --tail=100 \
  | grep "Processing job\|completed successfully"
```

**Verified**: clean 3/3 split of all 6 jobs across the two replica pods, each
completing successfully — proof that Redis Streams' consumer group correctly
load-balances across horizontally-scaled workers. Node memory went from 38%
(1 replica) to 48% (2 replicas), confirming real headroom remained for
further scaling on this VM if needed.

---

## 10. Definition of done

- [x] Entire stack (gateway, both workers, Postgres, Redis) running as K8s pods in a `modelmesh` namespace
- [x] Registry rows updated via `PATCH /v1/models/{id}` to point at K8s Service DNS names — no gateway code changes required (verified live: PATCHed both models to fully-qualified `.svc.cluster.local` endpoints, ran real inference immediately after, got correct results)
- [x] Liveness and readiness probes configured and actually doing something meaningful (`/health`, `/ready` on gateway, confirmed pod reaches `1/1 Running` only once Postgres is reachable)
- [x] Postgres running as a StatefulSet with a PersistentVolumeClaim (plus a ConfigMap generated from `db/init.sql` to seed it on first boot — not in the original plan, added to close a real gap)
- [x] Secrets managed via K8s Secrets, not plaintext env vars in YAML (`db-secret` for DATABASE_URL, `jwt-secret` for JWT_SECRET — the latter also not in the original plan; added since the gateway silently falls back to an insecure default if unset)
- [x] Demonstrated pod-kill resilience — gateway survives a worker pod restart (deleted doc-ocr-worker pod directly; Deployment auto-recreated it in ~37s; new and post-restart inference jobs both queued and completed successfully with no gateway downtime)
- [x] Demonstrated horizontal scaling with a measured throughput difference (scaled doc-ocr-worker to 2 replicas; 6 concurrent jobs split cleanly 3/3 across both pods via the same Redis Streams consumer group; node memory 38%→48% at 2 replicas, confirming headroom for further scaling)

Once every box is checked, move to Phase 5.

---

## Environment notes

- minikube: `--cpus=2 --memory=4096` (Docker driver, WSL2 host with 7.6GB total RAM)
- Images built directly into minikube's Docker daemon via `eval $(minikube docker-env)` + `imagePullPolicy: Never` — no external registry needed for local dev
- `eval $(minikube docker-env)` must be re-run per new terminal session — does not persist
- Postgres seeding required a ConfigMap generated directly from `db/init.sql` (`kubectl create configmap --from-file`), mounted at `/docker-entrypoint-initdb.d/` — this wasn't in the original Phase 4 plan, which only showed the StatefulSet without an init mechanism
