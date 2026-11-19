# Phase 5 — Observability + Canary Deployment

## Implementation Status: PARTIAL (75%)

**Goal:** Best-effort ClickHouse metrics and registry-driven canary routing. Prometheus instrumentation and Prometheus/Grafana are not implemented.

The 75% score is 9 of 12 scoped outcomes: Kubernetes ClickHouse deployment, table schema, doc-ocr metrics, ASR metrics, the canary registry field, canary routing, version pinning, canary verification, and the postmortem. The three not delivered are a Compose ClickHouse service, Prometheus instrumentation, and Prometheus/Grafana deployment.

**What's Done:** Kubernetes ClickHouse setup, final-outcome metrics from both workers, and canary routing logic
**What's Missing:** Compose ClickHouse, Prometheus instrumentation, and Grafana

---

## What Was Implemented ✓

### ClickHouse for Request Analytics - COMPLETE

- ClickHouse Kubernetes deployment + service
- `request_metrics` table schema (columnar, MergeTree engine)
- Best-effort metric logging from both workers
- Exactly one metric row per completed job, on its final outcome; retries that continue do not create rows
- Metric writes run off the event loop; after an insert failure, each worker skips further attempts for 60 seconds and emits at most one warning per back-off window
- Metrics include: request_id, model_id, model_name, model_version, worker_name, latency_ms, status
- Table DDL: [`clickhouse/init.sql`](../clickhouse/init.sql) (reconstructed; verify against a live table)

Every request that reaches a final outcome is logged exactly once by its worker. The gateway does not write metric rows. Jobs that never reach a final outcome, such as a queued job whose worker never completes it, produce no row. ClickHouse is deployed in Kubernetes only; Docker Compose has no ClickHouse service.

### Canary Routing Logic - COMPLETE

- `canary_percent` column added to `models` table
- `get_model_with_canary()` function in gateway
- Registry-driven traffic splitting (no K8s selector tricks)
- Version pinning support (`?version=v1` bypasses canary)
- Numeric version comparison: with two or more active versions, the highest is the canary and the lowest is stable; versions in between receive no traffic
- WebSocket inference has no canary routing
- The API-key rate limiter uses a fixed 60-second window
- Tested with v1/v2 split, verified via ClickHouse queries

### Prometheus Metrics Instrumentation - NOT IMPLEMENTED

There is no Prometheus client instrumentation or `/metrics` endpoint in the current services.

---

## What Was NOT Implemented ✗

### Prometheus + Grafana - NOT IMPLEMENTED

Prometheus instrumentation, Prometheus, and Grafana are not included in the current implementation.

ClickHouse remains Kubernetes-only. Docker Compose has no ClickHouse service; when a worker cannot reach ClickHouse, inference continues and that worker backs off metric inserts for 60 seconds after a failed attempt.

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
       count() AS successful_jobs,
       avg(latency_ms) AS avg_latency_ms,
       quantile(0.95)(latency_ms) AS p95_latency_ms
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR
  AND status = 'success'
GROUP BY model_name, model_version;
```

Latency statistics include successful jobs only. Calculate error rate separately across all final rows so errors remain in the denominator:

```sql
SELECT model_name, model_version,
       countIf(status = 'error') / count() AS error_rate
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR
GROUP BY model_name, model_version;
```

Each inference request that reaches a final outcome produces exactly one row. Retries that will continue produce no row until the job succeeds or reaches a terminal failure.

### 4. Promote or Rollback

- **Promote**: Increase `canary_percent` to 100, disable v1
- **Rollback**: Set `canary_percent` to 0. Do not disable v2 as part of rollback.

Both via `PATCH /v1/models/{id}`. Zero code changes.

## Canary Rollback Guard (Optional Phase 11)

`gateway/canary_guard.py` is a standalone CLI; it is not part of gateway request handling. It checks active model versions using ClickHouse final-outcome metrics. For each model with at least two active versions, the highest numeric version is the canary, the lowest is stable, and intermediate versions are ignored. If the highest version is already at 0%, the guard skips the ClickHouse query and reports `noop`. This keeps the required post-rollback noop run observable.

The guard uses two rollback rules:

- **Starvation:** estimates expected canary completions from stable completions and `canary_percent`. If expected canary traffic meets the minimum and observed canary completions are below 20% of expected, it rolls back. This catches workers that are down and therefore produce no error metrics.
- **Error-rate delta:** after both versions meet the minimum sample count, rolls back only when the canary error rate exceeds stable by more than the configured margin. If both versions fail at similar rates, it holds.

Defaults are a 10-minute sliding window, 30 minimum requests per version, and a 0.10 error-rate delta. Without `--apply`, the guard is a dry run and changes nothing. With `--apply`, it only sets the canary's `canary_percent` to 0; it does not disable or delete the canary.

Limitations:

- Queued jobs assigned to a rolled-back canary remain unprocessed until their payload and status keys expire; polling clients eventually receive 404.
- The window is a sliding look-back, not an exact count since the canary started.
- When stable traffic is thin, the guard holds rather than rolling back on insufficient data.

### Phase 11 live demos

Demo A used 90 ASR submissions at a 20% canary split: 73 were assigned to stable `v1` and 17 to the nonexistent `asr-worker-v2`. The dry run observed 73 stable completions and no canary completions, and returned `rollback` with the reason `canary starved: 0 completed vs about 18 expected`. The `--apply` run returned `rollback` with `applied: true`; a registry read confirmed `canary_percent=0`. The next run returned `noop` with `canary already at 0`.

Demo B used 10 **synthetic** `error` rows for `asr:v2`. With 73 successful stable `v1` rows and 10 canary error rows, the dry run returned `rollback` with `canary error rate 1.00 vs stable 0.00`; it did not apply the change. Cleanup reset the canary to 0, disabled the demo v2 model, and removed all 10 synthetic rows (verified count: 0).

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

### 4. ClickHouse failures do not fail jobs

Metric insert failures are caught and logged. Phase 10 moves the synchronous insert off the event loop and backs off further attempts for 60 seconds after a failure.

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
- [x] Both workers log one best-effort metric row per job's final outcome
- [x] Retries that will continue do not write rows; terminal failures use the failing attempt's elapsed time
- [x] ClickHouse host and port are configurable, with a 60-second per-worker back-off after insert failure
- [ ] Prometheus instrumentation and `/metrics` endpoints are not implemented
- [x] `canary_percent` field in models table
- [x] Registry-driven traffic splitting implemented
- [x] Version pinning support (bypass canary)
- [ ] Prometheus and Grafana are not implemented
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

**Why Best-Effort Metrics?**

- Observability outage should never become functional outage
- Inserts run off the event loop, are wrapped in try/except, and back off for 60 seconds after failure
- Metrics are best-effort

**Why Skip Prometheus/Grafana?**

- Prometheus instrumentation and the Prometheus/Grafana services are not implemented.
- The Compose stack does not include ClickHouse.

---

## What Would Change with More Time

1. **Deploy genuine v2**: Different preprocessing or confidence threshold, not same worker
2. **Add Prometheus + Grafana**: Real-time dashboards and instrumentation (not implemented)
3. **Statistical significance testing**: Don't promote on 1-2 samples, need real volume
4. **Multi-model canary**: Test framework with multiple models simultaneously

---

## Files Implemented

- `k8s/clickhouse/deployment.yaml` - ClickHouse Kubernetes deployment
- `k8s/clickhouse/service.yaml` - ClickHouse service
- `workers/doc_ocr/metrics.py` - Worker metrics with configurable host and failure back-off
- `workers/asr/metrics.py` - Worker metrics with configurable host and failure back-off
- `gateway/main.py` - Includes `get_model_with_canary()`
- `gateway/registry.py` - Added `canary_percent`, `list_versions()`
- `gateway/schemas.py` - Updated Pydantic models
- `gateway/canary_guard.py` - Optional standalone Phase 11 rollback guard
- `tests/test_canary_guard.py` - Guard decision and model-selection tests

---

## Production Readiness

**What's currently implemented:**

- Best-effort terminal-job ClickHouse writes from both workers when ClickHouse is available
- Canary routing logic
- Failure back-off and off-event-loop metric inserts

**What needs work:**

- Prometheus instrumentation and Prometheus + Grafana deployment
- Statistical significance checks and alerting for scheduled guard runs
- Production secret management for ClickHouse

---

## Postmortem Summary

Canary routing is registry-driven. Both workers write one best-effort ClickHouse metric row for each completed job; the gateway does not write metrics, and unfinished jobs have no row. ClickHouse is Kubernetes-only, not in Compose. Prometheus instrumentation and Prometheus/Grafana are not implemented.

**Interview talking point**: "Built registry-driven canary routing, so model-version traffic splits are controlled by registry data. Both workers write one best-effort ClickHouse metric row per completed job for version, latency, and final status; the gateway does not write metrics, and ClickHouse is not included in Docker Compose. An optional standalone guard detects canary starvation or error-rate regressions and can set the percentage to zero; Prometheus/Grafana are not implemented."
