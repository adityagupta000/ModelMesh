# Phase 5 — Observability + Canary Deployment

### ModelMesh Implementation Guide

**Goal:** Full metrics pipeline (ClickHouse + Prometheus + Grafana), and one real canary rollout driven entirely through the Model Registry — registering a new version, not editing gateway code or K8s selectors by hand.

**Estimated time:** 5–7 days

**Prerequisite:** Phase 4 fully complete and checked off.

---

## Part A: ClickHouse for request analytics

### 1. Why ClickHouse and not just Postgres

Your `requests` table in Postgres is fine for transactional lookups (auth, rate limiting), but it's the wrong tool for analytical queries like "p95 latency over the last hour, grouped by model and version." ClickHouse is a columnar store built exactly for that kind of aggregation at scale — which is why it's on the JD alongside Postgres and Redis rather than instead of them.

### 2. Schema — model-and-version aware

```sql
CREATE TABLE request_metrics (
    request_id UUID,
    model_id UUID,
    model_name String,
    model_version String,
    worker_name String,
    status String,
    latency_ms UInt32,
    timestamp DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (model_name, model_version, timestamp);
```

Carrying `model_version` here is what makes the canary comparison in Part C possible — without it you can't tell v1's numbers apart from v2's.

### 3. Writing to ClickHouse

Write metrics asynchronously — either via a lightweight background task, or by having your Phase 2 queue consumer emit a metrics event after processing.

```python
# gateway/metrics.py
from clickhouse_connect import get_client

ch_client = get_client(host="clickhouse", port=8123)

async def log_metric(request_id, model, status, latency_ms):
    ch_client.insert(
        "request_metrics",
        [[request_id, model.id, model.name, model.version, model.worker_name, status, latency_ms]],
        column_names=["request_id", "model_id", "model_name", "model_version", "worker_name", "status", "latency_ms"]
    )
```

### 4. Example analytical queries (you'll use these for the Grafana dashboard)

```sql
-- p95 latency per model + version, last hour (this is your canary comparison query)
SELECT
    model_name,
    model_version,
    quantile(0.95)(latency_ms) AS p95_latency
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR
GROUP BY model_name, model_version;

-- error rate per model + version
SELECT
    model_name,
    model_version,
    countIf(status = 'error') / count() AS error_rate
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR
GROUP BY model_name, model_version;
```

---

## Part B: Prometheus + Grafana

### 1. Instrument the gateway and workers

```python
from prometheus_client import Counter, Histogram

REQUEST_COUNT = Counter("modelmesh_requests_total", "Total requests", ["model_name", "model_version", "status"])
REQUEST_LATENCY = Histogram("modelmesh_request_latency_ms", "Latency", ["model_name", "model_version"])

REQUEST_COUNT.labels(model_name=model.name, model_version=model.version, status="success").inc()
REQUEST_LATENCY.labels(model_name=model.name, model_version=model.version).observe(latency_ms)
```

Labeling by `model_version` from the start means your canary dashboard in Part C needs zero new instrumentation — it's the same metrics, just filtered.

### 2. Prometheus config

```yaml
# prometheus.yml
scrape_configs:
  - job_name: "gateway"
    kubernetes_sd_configs:
      - role: pod
        namespaces: { names: ["modelmesh"] }
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: gateway
        action: keep
```

Deploy Prometheus and Grafana into the cluster via Helm:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm install prometheus prometheus-community/prometheus -n modelmesh
helm install grafana grafana/grafana -n modelmesh
```

### 3. Grafana dashboard — what to actually put on it

- Requests/sec by model name + version
- p50/p95/p99 latency by model name + version (this is your canary panel)
- Error rate over time, split by version
- Queue depth / consumer lag per `worker_name` (from Phase 2)
- Pod restart count (proves your Phase 4 resilience work is visible, not just tested once)

---

## Part C: Canary deployment — driven by the registry

### 1. The setup — register v2, don't hand-edit anything

Deploy a "v2" worker (even a deliberately small change is enough — what matters is the mechanism). Because of the registry design from Phase 1, promoting a canary is a **registration**, not a code change:

```bash
curl -X POST http://gateway/v1/models \
  -H "Authorization: Bearer <admin-key>" \
  -d '{
    "name": "plant-health",
    "version": "v2",
    "worker_name": "plant-worker-v2",
    "protocol": "grpc",
    "endpoint": "plant-health-worker-v2.modelmesh.svc.cluster.local:50051",
    "description": "v2 - retrained with updated confidence thresholds"
  }'
```

v1 and v2 now coexist as two rows in the `models` table, same `name`, different `version` — exactly the schema built in Phase 1 for this purpose.

### 2. Traffic splitting

The gateway needs a small addition here: when a client requests `plant-health` without specifying a version, decide the split in the registry lookup itself rather than in K8s:

```python
async def get_model_with_canary(db, model_name: str, canary_percent: dict):
    """canary_percent e.g. {'plant-health': {'v2': 10}} means 10% of traffic to v2."""
    import random
    versions = await registry.list_versions(db, model_name)
    if model_name in canary_percent:
        roll = random.randint(1, 100)
        threshold = canary_percent[model_name].get('v2', 0)
        version = 'v2' if roll <= threshold else 'v1'
    else:
        version = 'v1'
    return await registry.get_model(db, model_name, version)
```

This is arguably a cleaner mechanism than the Kubernetes replica-ratio trick, and it's a direct product of having built the registry — worth pointing out explicitly if asked "why did you design it this way."

If you want the K8s-native version instead (simpler code, coarser control): run v1 at 9 replicas and v2 at 1 replica behind the same Service selector (matching only `app: plant-health-worker`, not `version`), letting K8s's round-robin do the split. Either approach is honest; pick one and be able to explain the tradeoff against the other.

### 3. The actual canary process

1. Register v2 at 10% traffic (via the registry, per above)
2. Watch the Grafana dashboard for 15–30 minutes: compare v2's error rate and latency against v1's, using the `model_version` label already in your metrics
3. Make a real decision based on the data: promote (raise v2's percentage toward 100, then disable v1 via `PATCH /v1/models/{v1_id}` with `status: disabled`) or roll back (disable v2)
4. Record what you saw — even if v2 performed identically to v1, that's a valid, honest result to report

### 4. Write the postmortem-style doc

One page, plain language:

- What you changed in v2
- What the dashboard showed during the canary window
- What decision you made and why
- What you'd do differently with more time (e.g. automated rollback triggers, statistical significance testing before promoting)

This document is arguably the single most interview-useful artifact from the entire project — it's proof of judgment, not just implementation.

---

## 5. Definition of done

- [ ] Every request logged to ClickHouse with model_name, model_version, worker_name, status, latency
- [ ] Prometheus scraping gateway and worker metrics, labeled by model_name and model_version
- [ ] Grafana dashboard showing requests/sec, p50/p95/p99 latency, error rate — filterable by version
- [ ] A real v2 registered through `POST /v1/models`, coexisting with v1
- [ ] A real canary rollout executed via registry-driven traffic splitting (or the K8s replica-ratio alternative), observed, and either promoted or rolled back based on actual dashboard data
- [ ] One-page postmortem doc written

---

## After Phase 5: what you actually have

At this point you have a working, observable, orchestrated, multi-model inference **platform** — not an app that happens to serve two models — with a registry that let every later phase (queueing, gRPC, Kubernetes migration, canary) plug in without rewriting the gateway. Go back to the resume bullets and fill in real numbers, not placeholders. The registry itself is worth its own line: _"Designed a database-backed Model Registry enabling new models to be added via API with zero gateway code changes, supporting versioned canary rollouts."_
