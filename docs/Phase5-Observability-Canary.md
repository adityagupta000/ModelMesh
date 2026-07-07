# Phase 5 — Observability + Canary Deployment

## Implementation Status: PARTIAL (70% Complete)

**Goal:** Full metrics pipeline (ClickHouse + Prometheus + Grafana) and registry-driven canary deployment.

**What's Done:** ClickHouse setup, metrics instrumentation, canary routing logic  
**What's Missing:** Prometheus + Grafana (scoped out due to minikube resource constraints)

---

## What Was Implemented ✓

### ClickHouse for Request Analytics - COMPLETE

- ClickHouse Kubernetes deployment + service
- `request_metrics` table schema (columnar, MergeTree engine)
- Async metric logging from gateway and workers
- Fire-and-forget metric writes (never block inference)
- Metrics include: model_name, model_version, worker_name, latency_ms, status

### Canary Routing Logic - COMPLETE

- `canary_percent` column added to `models` table
- `get_model_with_canary()` function in gateway
- Registry-driven traffic splitting (no K8s selector tricks)
- Version pinning support (`?version=v1` bypasses canary)
- Tested with v1/v2 split, verified via ClickHouse queries

### Prometheus Metrics Instrumentation - COMPLETE

- `prometheus-client` added to all services
- Counter: `modelmesh_requests_total` (labels: model_name, model_version, status)
- Histogram: `modelmesh_request_latency_ms` (labels: model_name, model_version)
- Metrics endpoint `/metrics` exposed

---

## What Was NOT Implemented ✗

### Prometheus + Grafana - SCOPED OUT

**Reason**: Minikube resource constraints (2 CPU / 4GB RAM). With ClickHouse, gateway, Postgres, Redis, and workers running, no headroom for Prometheus + Grafana.

**Workaround**: Direct ClickHouse SQL queries prove the data pipeline works. Grafana would add visualization layer without new verification value.

**Production note**: In real deployment with adequate resources, Prometheus + Grafana should be added. Metrics instrumentation is already in place.

---

## How Canary Works (Implemented)

### 1. Register v2 via API (not kubectl)

```bash
curl -X POST http://gateway/v1/models \
  -H "Authorization: Bearer <admin-key>" \
  -d '{
    "name": "doc-ocr",
    "version": "v2",
    "worker_name": "doc-ocr-worker-v2",
    "canary_percent": 10,
    "protocol": "grpc",
    "endpoint": "doc-ocr-worker-v2.modelmesh.svc.cluster.local:50051"
  }'
```

v1 and v2 coexist as separate rows, same `name`, different `version`.

### 2. Traffic Splitting (Registry-Driven)

Gateway's `get_model_with_canary()` function:

- Client specifies version → Route to that version
- Client omits version → Roll random 1-100
  - ≤ canary_percent → Route to highest version (v2)
  - \> canary_percent → Route to lowest version (v1)

**Key insight**: This is simpler and more flexible than K8s replica-ratio tricks. Traffic split is a data change, not a deployment.

### 3. Monitor via ClickHouse

```sql
SELECT model_name, model_version,
       count() as requests,
       avg(latency_ms) as avg_latency,
       quantile(0.95)(latency_ms) as p95_latency,
       countIf(status = 'error') / count() as error_rate
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR
GROUP BY model_name, model_version;
```

### 4. Promote or Rollback

- **Promote**: Increase `canary_percent` to 100, disable v1
- **Rollback**: Set `canary_percent` to 0, disable v2

Both via `PATCH /v1/models/{id}`. Zero code changes.

---

## Bugs Found and Fixed

### 1. Version-pinning ambiguity

**Problem**: `version: str = "v1"` made "explicit v1" and "no preference" indistinguishable.  
**Fix**: Changed to `version: str | None = None`. Only trigger canary when `None`.

### 2. Missing `supports_streaming` field

**Problem**: `get_model_by_id` and `list_models` didn't include `supports_streaming` in `ModelRecord`.  
**Fix**: Added field to all registry functions.

### 3. ClickHouse authentication

**Problem**: Official image now enforces default-user password.  
**Fix**: Local dev uses `CLICKHOUSE_SKIP_USER_SETUP=1` (NOT production-safe).

### 4. Fire-and-forget worked perfectly

**Observation**: During ClickHouse auth failure, job processing never broke. Only metric write silently failed with warning. This is the entire point of try/except wrapper.

---

## Verification

### Canary Split Test

- 20 requests with no version → Split 10/10 between v1/v2 at 50% canary
- 5 requests with `?version=v1` → All landed on v1
- ClickHouse query correctly separated metrics by `model_version`

**Note**: v1 and v2 pointed at identical worker (proof of mechanism, not real rollout). Real canary would deploy genuinely different v2 worker.

---

## Definition of Done

- [x] ClickHouse deployment + service in Kubernetes
- [x] `request_metrics` table with model_name, model_version, worker_name
- [x] Every request logged to ClickHouse
- [x] Prometheus metrics instrumented (labeled by model_name, model_version)
- [x] `canary_percent` field in models table
- [x] Registry-driven traffic splitting implemented
- [x] Version pinning support (bypass canary)
- [ ] Prometheus deployed and scraping (scoped out)
- [ ] Grafana dashboard showing requests/sec, p95 latency, error rate (scoped out)
- [x] Real canary test executed and verified
- [x] Postmortem doc written

---

## Key Design Decisions

**Why ClickHouse?**

- Columnar store built for analytical queries
- `GROUP BY model_version` at scale (Postgres not designed for this)
- Separate transactional (Postgres) from analytical (ClickHouse) workloads

**Why Registry-Driven Canary?**

- Traffic split via data, not K8s replicas
- More flexible (per-model percentages)
- Easier to automate (API call, not kubectl)
- Works with any orchestrator, not K8s-specific

**Why Fire-and-Forget Metrics?**

- Observability outage should never become functional outage
- Wrapped in try/except, only log warnings
- Metrics are best-effort

**Why Skip Prometheus/Grafana?**

- Resource constraints (minikube 4GB RAM)
- ClickHouse queries prove data pipeline works
- Visualization layer adds polish, not verification value
- Production deployment should add them

---

## What Would Change with More Time

1. **Deploy genuine v2**: Different preprocessing or confidence threshold, not same worker
2. **Add Prometheus + Grafana**: Real-time dashboards (requires more resources)
3. **Automated rollback triggers**: Auto-disable v2 if error rate exceeds threshold
4. **Statistical significance testing**: Don't promote on 1-2 samples, need real volume
5. **Multi-model canary**: Test framework with multiple models simultaneously

---

## Files Implemented

- `k8s/clickhouse/deployment.yaml` - ClickHouse Kubernetes deployment
- `k8s/clickhouse/service.yaml` - ClickHouse service
- `gateway/metrics.py` - ClickHouse metric logging
- `workers/doc_ocr/metrics.py` - Worker metrics
- `workers/asr/metrics.py` - Worker metrics
- `gateway/main.py` - Updated with `get_model_with_canary()` + Prometheus metrics
- `gateway/registry.py` - Added `canary_percent`, `list_versions()`
- `gateway/schemas.py` - Updated Pydantic models

---

## Production Readiness

**What's production-ready:**

- ClickHouse data pipeline
- Canary routing logic
- Metrics instrumentation
- Fire-and-forget safety

**What needs work:**

- Prometheus + Grafana deployment
- Automated rollback rules
- Statistical significance checks
- Production secret management for ClickHouse

---

## Postmortem Summary

Canary mechanism proven: registered v2, split traffic via registry, verified split in ClickHouse. Metrics pipeline works end-to-end. Prometheus/Grafana deferred due to resource constraints, but instrumentation in place for easy addition. Registry-driven approach (Phase 1's design) enabled zero-code canary rollout.

**Interview talking point**: "Built a registry-driven canary system where promoting a new model version is an API call, not a code deploy. Metrics show per-version latency and error rates in ClickHouse. Scoped Grafana out due to local resource limits, but the hard part — data pipeline and routing logic — is done."
