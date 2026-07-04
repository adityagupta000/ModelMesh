# Phase 4 — Kubernetes Deployment

### ModelMesh Implementation Guide

**Goal:** Move the whole stack off Docker Compose onto real orchestration — gateway and workers as independently scalable pods, with the Model Registry updated to point at K8s Service DNS names instead of Docker Compose service names.

**Estimated time:** 5–7 days

**Prerequisite:** Phase 3 fully complete and checked off.

---

## 1. Local cluster setup

Use **minikube** (simpler, good docs) or **k3s** (lighter weight, closer to production feel). Either is fine — pick one and don't waste time agonizing over it.

```bash
# minikube
minikube start --cpus=4 --memory=8192
minikube addons enable metrics-server   # needed for autoscaling later
```

Point your local Docker builds at minikube's Docker daemon so images don't need to be pushed to a registry:

```bash
eval $(minikube docker-env)
docker build -t modelmesh-gateway:latest ./gateway
docker build -t modelmesh-plant-health:latest ./workers/plant_health
docker build -t modelmesh-asr:latest ./workers/asr
```

---

## 2. Namespace and structure

```
k8s/
├── namespace.yaml
├── gateway/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── configmap.yaml
├── workers/
│   ├── plant-health-deployment.yaml
│   ├── plant-health-service.yaml
│   ├── asr-deployment.yaml
│   └── asr-service.yaml
├── postgres/
│   ├── statefulset.yaml
│   └── service.yaml
├── redis/
│   ├── deployment.yaml
│   └── service.yaml
└── secrets.yaml
```

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: modelmesh
```

---

## 3. Gateway deployment

```yaml
# gateway/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway
  namespace: modelmesh
spec:
  replicas: 2
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

Add `/health` (basic liveness — is the process up) and `/ready` (readiness — can it actually serve, e.g. DB connection and registry query both work) endpoints to your FastAPI app if you don't have them yet.

---

## 4. Worker deployments

Same pattern as gateway, but each worker gets its own Deployment + Service so they're independently scalable and independently addressable by DNS name.

```yaml
# workers/plant-health-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: plant-health-worker
  namespace: modelmesh
spec:
  replicas: 2
  selector:
    matchLabels: { app: plant-health-worker }
  template:
    metadata:
      labels: { app: plant-health-worker }
    spec:
      containers:
        - name: plant-health-worker
          image: modelmesh-plant-health:latest
          imagePullPolicy: Never
          ports: [{ containerPort: 50051 }]
          resources:
            requests: { cpu: "500m", memory: "1Gi" }
            limits: { cpu: "1", memory: "2Gi" }
```

---

## 5. Update the Model Registry — this replaces manual gateway config

This is the key step that's different because of Phase 1's registry design. Instead of editing gateway code or environment variables to point at the new K8s DNS names, you update the registry rows:

```bash
curl -X PATCH http://gateway/v1/models/<plant-health-model-id> \
  -H "Authorization: Bearer <admin-key>" \
  -d '{"endpoint": "plant-health-worker.modelmesh.svc.cluster.local:50051"}'

curl -X PATCH http://gateway/v1/models/<asr-model-id> \
  -H "Authorization: Bearer <admin-key>" \
  -d '{"endpoint": "asr-worker.modelmesh.svc.cluster.local:50051"}'
```

No gateway redeploy needed for this change — it's a data update, not a code change. This is worth explicitly calling out as a benefit of the registry design when you talk about this project: **migrating workers from Docker Compose to Kubernetes required zero gateway code changes, only registry updates.** That sentence, backed by a real PATCH request you actually ran, is a strong, honest thing to say in an interview.

---

## 6. Postgres as a StatefulSet (not a Deployment)

Postgres needs stable storage and identity, so it belongs in a StatefulSet with a PersistentVolumeClaim, not a regular Deployment:

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
          envFrom:
            - secretRef: { name: db-secret }
          volumeMounts:
            - name: pgdata
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata: { name: pgdata }
      spec:
        accessModes: ["ReadWriteOnce"]
        resources: { requests: { storage: 2Gi } }
```

Your `models` table (the registry) lives in this same Postgres instance — it's the source of truth the gateway reads on every request, so its durability matters as much as your `users`/`api_keys` tables.

---

## 7. Secrets

```bash
kubectl create secret generic db-secret \
  --namespace modelmesh \
  --from-literal=url=postgresql://user:pass@postgres:5432/modelmesh
```

Never commit real secrets to the repo — reference `secrets.yaml` as a template with placeholder values, and note in your README how to create the real one.

---

## 8. Deploy and verify

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/ -n modelmesh --recursive
kubectl get pods -n modelmesh -w
```

Test resilience — this is the actual point of the exercise:

```bash
kubectl delete pod <plant-health-worker-pod-name> -n modelmesh
kubectl get pods -n modelmesh -w
```

Confirm the gateway keeps serving requests (maybe with a brief blip) while the pod restarts.

---

## 9. Horizontal scaling test

```bash
kubectl scale deployment plant-health-worker --replicas=4 -n modelmesh
```

Re-run your Artillery load test against the gateway and see whether throughput actually improves with more worker replicas. Record the numbers — "I scaled it and it worked" is weak; "I scaled from 2 to 4 replicas and throughput went from X to Y req/s" is strong.

---

## 10. Definition of done

- [ ] Entire stack (gateway, both workers, Postgres, Redis) running as K8s pods in a `modelmesh` namespace
- [ ] Registry rows updated via `PATCH /v1/models/{id}` to point at K8s Service DNS names — no gateway code or env var changes
- [ ] Liveness and readiness probes configured and actually doing something meaningful
- [ ] Postgres running as a StatefulSet with a PersistentVolumeClaim
- [ ] Secrets managed via K8s Secrets, not plaintext env vars in YAML
- [ ] Demonstrated pod-kill resilience — gateway survives a worker pod restart
- [ ] Demonstrated horizontal scaling with a measured throughput difference

Once checked off, move to Phase 5.
