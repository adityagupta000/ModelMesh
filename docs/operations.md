# Operations

Configuration reference, sizing, monitoring, routine tasks and troubleshooting. For first-time setup see [Getting started](getting-started.md).

## Configuration reference

Every environment variable the code reads (found by searching the whole codebase). Defaults are the values in code.

### Gateway

| Variable              | Default                                      | Meaning                                                                                                        |
| --------------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`        | `postgresql://user:pass@localhost/modelmesh` | Postgres connection. Compose uses `…@postgres/modelmesh`; Kubernetes reads it from `db-secret`                 |
| `REDIS_URL`           | `redis://localhost:6379`                     | Redis connection                                                                                               |
| `JWT_SECRET`          | `change-me-in-production`                    | JWT signing key. If left at the default a warning is logged but the gateway still starts                       |
| `MAX_UPLOAD_BYTES`    | `10485760` (10 MiB)                          | Upload cap for REST and WebSocket; also sets uvicorn's `--ws-max-size` and the gRPC message limit (plus 1 MiB) |
| `PAYLOAD_TTL_SECONDS` | `3600`                                       | Lifetime of `payload:{job_id}`                                                                                 |
| `STREAM_MAXLEN`       | `10000`                                      | Approximate cap of `inference_jobs`                                                                            |

### Workers

| Variable           | Default                              | Meaning                                                                        |
| ------------------ | ------------------------------------ | ------------------------------------------------------------------------------ |
| `WORKER_NAME`      | `doc-ocr-worker` / `asr-worker`      | Consumer group is `{WORKER_NAME}_group`; must equal the registry `worker_name` |
| `REDIS_URL`        | `redis://redis:6379`                 |                                                                                |
| `RECLAIM_IDLE_MS`  | `300000`                             | How long an entry must be idle before another consumer reclaims it             |
| `STREAM_MAXLEN`    | `10000`                              | Cap applied when a retry re-adds an entry                                      |
| `GRPC_MAX_WORKERS` | `10` (3 in the Kubernetes manifests) | gRPC server threads                                                            |
| `MAX_UPLOAD_BYTES` | `10485760`                           | gRPC message size limit (plus 1 MiB)                                           |
| `WHISPER_MODEL`    | `tiny`                               | ASR only. The image preloads `tiny`; another size would download at start-up   |
| `CLICKHOUSE_HOST`  | unset                                | **Unset disables metrics.** Set to `clickhouse` in the Kubernetes manifests    |
| `CLICKHOUSE_PORT`  | `8123`                               |                                                                                |
| `HOSTNAME`         | `1`                                  | Part of the consumer name; the container runtime sets it                       |

`PAYLOAD_TTL_SECONDS` is also set on the workers in Compose and Kubernetes, but **worker code never reads it** (the worker TTLs below are fixed). Setting it there does nothing.

### Canary guard (CLI)

`GATEWAY_URL` (default `http://localhost:8000`), `ADMIN_KEY` (required with `--apply`), `CLICKHOUSE_HOST` (default `clickhouse`), `CLICKHOUSE_PORT` (default 8123). See [Registry and canary](registry-and-canary.md#the-rollback-guard).

### Tests

`TEST_DATABASE_URL` and `TEST_REDIS_URL`; see [Testing](testing.md).

### Fixed in code (not configurable)

| Value                                   | Setting                                       |
| --------------------------------------- | --------------------------------------------- |
| Job status TTL / result TTL / owner TTL | 3600 s / 7200 s / 7200 s                      |
| Retries and backoff                     | 3 retries, sleeps of 2, 4, 8 s                |
| Dead-letter stream cap                  | about 1,000 entries                           |
| Consumer read                           | 1 entry, block 5 s                            |
| Maintenance interval                    | 30 s                                          |
| Rate-limit window                       | 60 s                                          |
| JWT lifetime                            | 24 hours                                      |
| Gateway to worker HTTP timeout          | 30 s                                          |
| gRPC timeouts                           | 30 s (unary), 60 s (stream)                   |
| ClickHouse timeouts / back-off          | 2 s connect, 5 s send and receive / 60 s skip |
| Canary guard starvation threshold       | 20% of expected                               |

## Resource sizing

### Reported memory (Docker Compose, Phase 9 report)

Measured by the project author with `docker stats` after smoke runs. Small synthetic inputs only; **not** a large-document or load result.

| Container      |    Memory |
| -------------- | --------: |
| gateway        |  66.5 MiB |
| doc-ocr worker | 1.185 GiB |
| asr worker     | 1.084 GiB |
| Redis          |  20.0 MiB |
| Postgres       |  31.8 MiB |

Each worker loads its model in three processes ([Architecture](architecture.md#worker-container-process-model)), which is why these are large.

### Kubernetes requests and limits (from the manifests)

| Workload       | CPU request / limit | Memory request / limit            |
| -------------- | ------------------- | --------------------------------- |
| gateway        | 250m / 500m         | 256Mi / 512Mi                     |
| postgres       | 250m / 500m         | 256Mi / 512Mi                     |
| redis          | 100m / 250m         | 128Mi / 256Mi (`maxmemory 200mb`) |
| clickhouse     | 250m / 500m         | 400Mi / 700Mi                     |
| asr-worker     | 500m / 1 CPU        | 1200Mi / 1800Mi                   |
| doc-ocr-worker | 500m / 1 CPU        | 1Gi / 3Gi                         |

Recorded on minikube: the OCR container's cgroup peak was **2,503 MiB**; limits of 1.5 GiB and 2 GiB each caused out-of-memory kills at start-up, so the limit is 3 GiB. Probes: gateway liveness `/health` (5 s initial, 10 s period) and readiness `/ready` (3 s, 5 s); workers readiness `/health` (30 s, 10 s) and liveness `/health` (60 s, 20 s, three failures).

Redis at 200 MB with no eviction is easy to fill with large uploads (see [Data stores](data-stores.md#memory-limits-and-what-a-full-redis-looks-like-verified)). Size it for `MAX_UPLOAD_BYTES` times the number of uploads you expect inside one `PAYLOAD_TTL_SECONDS`.

## Monitoring

### Queue

```bash
# Compose
docker compose exec redis redis-cli XLEN inference_jobs
docker compose exec redis redis-cli XPENDING inference_jobs doc-ocr-worker_group
docker compose exec redis redis-cli XPENDING inference_jobs asr-worker_group
docker compose exec redis redis-cli XREAD COUNT 10 STREAMS inference_jobs_dlq 0

# Kubernetes
kubectl exec -n modelmesh deployment/redis -- redis-cli XLEN inference_jobs
```

| Reading                                                      | Means                                                                                                                              |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `XLEN inference_jobs` close to `STREAM_MAXLEN`               | Entries (including acknowledged ones) are being trimmed; a backlog this long can lose unread jobs                                  |
| `XPENDING` count staying above zero with a growing idle time | A consumer took entries and has not finished; they are reclaimed after `RECLAIM_IDLE_MS`                                           |
| Entries in `inference_jobs_dlq`                              | Jobs that failed permanently or ran out of retries. The `reason` is the real error. There is **no replay tool**; resubmit manually |

Note that `XLEN` includes acknowledged entries: acknowledging does not delete.

### Logs

Workers log each job (`Processing job …`, `completed successfully (N ms)`), retries and failures (the OCR worker logs these; the ASR worker does not), and a maintenance line about every 30 seconds with the stream length and pending count. A metrics problem logs `metric insert failed (skipping for 60s)` once per back-off window. The gateway logs through uvicorn, with `api_key=` redacted.

### Metrics (Kubernetes with ClickHouse only)

Use the queries in [Data stores](data-stores.md#useful-queries). There is no Prometheus, no Grafana and no `/metrics` endpoint, and the health endpoints do not report queue depth or memory.

### Health

- `/ready` = Postgres and Redis answer. It does **not** notice a full Redis or a missing worker.
- `/health` on the gateway and workers always reports ok.
- `kubectl top pods -n modelmesh` needs `minikube addons enable metrics-server`.

## Routine tasks

| Task                   | How                                                                                                                                                                                                 |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Add capacity           | Kubernetes: `kubectl scale deployment <worker> -n modelmesh --replicas=N`. Compose publishes fixed host ports for each worker, so `--scale` would collide; run extra replicas on Kubernetes instead |
| Disable a model        | `DELETE /v1/models/{id}` or `PATCH` `status: disabled` (already-queued jobs still run)                                                                                                              |
| Add a model or version | [Registry and canary](registry-and-canary.md#adding-a-model-or-a-version)                                                                                                                           |
| Run a canary           | [Registry and canary](registry-and-canary.md#running-a-canary)                                                                                                                                      |
| Change the schema      | Edit `db/init.sql`, add an **idempotent** file in `migrations/`, apply it by hand to existing databases, and copy the SQL into `k8s/postgres/init-configmap.yaml`                                   |
| Rotate `JWT_SECRET`    | Restart the gateway with the new value. Existing JWTs stop working; API keys are unaffected                                                                                                         |
| Revoke an API key      | `UPDATE api_keys SET revoked = TRUE WHERE …` in SQL (no API)                                                                                                                                        |
| Grant admin            | SQL, see [Getting started](getting-started.md#becoming-an-admin)                                                                                                                                    |
| Back up                | Postgres only (users, keys, registry). Redis contents are transient and not durable in Kubernetes                                                                                                   |

Deploy order, as recorded in the Phase 2 documentation: workers accept both the old (payload inside the stream entry) and the current (separate payload key) job format, so **deploy workers before the gateway**, wait for the queue to drain, then deploy the gateway. Rolling the gateway back is safe.

## Troubleshooting

```mermaid
flowchart TD
    A["Job stays queued"] --> B{"Is a worker running whose WORKER_NAME<br/>equals the model's worker_name?"}
    B -->|"no"| C["Start it, or correct worker_name in the registry.<br/>Jobs for it are silently skipped by other workers"]
    B -->|"yes"| D{"Do worker logs show the maintenance line<br/>about every 30 s?"}
    D -->|"no"| E["Consumer process is down. The container should restart<br/>if any process exits; check logs and memory limits"]
    D -->|"yes"| F{"XPENDING idle for minutes?"}
    F -->|"yes"| G["A consumer is stuck or died. Entries are reclaimed<br/>after RECLAIM_IDLE_MS"]
    F -->|"no"| H["Backlog, or the job already expired.<br/>Status lasts 1 h, then 404"]
```

| Symptom                                                              | Likely cause and action                                                                                                                                                                        |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /v1/models` returns 500, log shows `KeyError: 'canary_percent'` | The database predates migration 004. Apply `migrations/004_canary_percent_and_constraints.sql`                                                                                                 |
| Model list is missing the seeded models                              | `init.sql` did not run: it only runs on an empty Postgres volume                                                                                                                               |
| Upload returns **500**                                               | Redis is full (`noeviction`) or unreachable. Check `redis-cli INFO memory`. `/ready` can still say ready                                                                                       |
| Upload returns 413                                                   | Larger than `MAX_UPLOAD_BYTES`                                                                                                                                                                 |
| Upload returns 429                                                   | The key's `rate_limit_per_min` for the current 60 s window. There is no `Retry-After`; wait up to a minute                                                                                     |
| `503 Model is currently disabled`                                    | `status` is `disabled`, or you pinned a disabled version                                                                                                                                       |
| `404 Unknown model` for an existing model                            | Its only active version is not `v1` and you did not pin it, or the only version is disabled and not `v1`; see [Registry and canary](registry-and-canary.md#how-a-model-version-is-chosen-rest) |
| `404 Job not found or expired`                                       | Expired (about 1 to 2 h), wrong job ID, or the job belongs to another user                                                                                                                     |
| Job `failed` with `Invalid image data` or `Invalid audio data`       | Permanent input error; no retries were attempted                                                                                                                                               |
| Job `failed` after about 14 s of waiting                             | A transient error was retried 3 times; the `error` field has the last message                                                                                                                  |
| Jobs complete but ClickHouse has no rows                             | `CLICKHOUSE_HOST` unset, DDL not applied, or the back-off window; look for `metric insert failed`                                                                                              |
| Canary receives no traffic                                           | `canary_percent` is set on the wrong row (it is read from the highest version), the model has fewer than two active versions, or you are using the WebSocket path or `?version=`               |
| WebSocket closes 4401 / 4404 / 4503 / 1009                           | Bad or missing key / unknown model or version / disabled model / message above `MAX_UPLOAD_BYTES`                                                                                              |
| WebSocket error text includes `for url 'http://…'`                   | The worker returned an HTTP error; the message is the raw exception. Fix the worker                                                                                                            |
| Worker container restarts repeatedly                                 | One of its three processes exited; check logs. On Kubernetes an out-of-memory kill is likely if the limit is below about 2.5 GiB for OCR                                                       |
| Gateway cannot import `inference_pb2`                                | The protobuf stubs were not generated. They are built at image build time (gateway) and container start (workers) from `inference.proto`                                                       |
| Tests leave rows behind                                              | Expected; use a scratch database ([Testing](testing.md#test-hygiene))                                                                                                                          |
| Data disappeared after `docker compose down -v`                      | `-v` deletes the Postgres and Redis volumes. Recreate users, keys and models                                                                                                                   |
| Redis restart lost jobs on Kubernetes                                | Redis has no volume there; this is expected                                                                                                                                                    |
