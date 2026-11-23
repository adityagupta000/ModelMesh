# Data stores

What lives in Postgres, Redis and ClickHouse, with exact keys, lifetimes and the places where the schema and the code disagree. Behaviour marked **verified** was observed on a real Postgres 16 and Redis 7.0.15 in the review environment.

| Store               | Holds                                                             | Required?                                          |
| ------------------- | ----------------------------------------------------------------- | -------------------------------------------------- |
| **Postgres 16**     | Users, API keys, the model registry                               | Yes                                                |
| **Redis 7**         | Job stream, payloads, job status and results, rate-limit counters | Yes                                                |
| **ClickHouse 24.8** | One row per finished job (metrics)                                | No. Kubernetes only, and only if you apply the DDL |

## Postgres

### Schema

```mermaid
erDiagram
    users ||--o{ api_keys : "owns"
    api_keys ||--o{ requests : "unused"
    models ||--o{ requests : "unused"
    users {
        uuid id PK
        text email UK
        text password_hash
        timestamptz created_at
    }
    api_keys {
        uuid id PK
        uuid user_id FK
        text key_hash UK
        int rate_limit_per_min
        bool is_admin
        bool revoked
        timestamptz created_at
    }
    models {
        uuid id PK
        text name
        text version
        text worker_name
        text protocol
        text endpoint
        text status
        bool supports_streaming
        int canary_percent
        text description
        timestamptz created_at
    }
    requests {
        uuid id PK
        uuid api_key_id FK
        uuid model_id FK
        text model_type
        text status
        int latency_ms
        timestamptz created_at
    }
```

| Table      | Notes                                                                                                                                                                                                                                               |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `users`    | `password_hash` is bcrypt. Email is unique                                                                                                                                                                                                          |
| `api_keys` | Only the **SHA-256 hex** of the key is stored; the raw key is shown once at creation. `rate_limit_per_min` defaults to 60. `is_admin` and `revoked` default to false. Deleting a user cascades to their keys                                        |
| `models`   | `UNIQUE (name, version)`. Constraints (named): `protocol` in `http`/`grpc`; `status` in `active`/`disabled`/`deleted`; `canary_percent` between 0 and 100 (default 0, not null). `supports_streaming` defaults to false. `version` defaults to `v1` |
| `requests` | **Never written or read by any code.** It exists in `db/init.sql` and the Kubernetes ConfigMap only (checked by searching the whole repository)                                                                                                     |

The extension `pgcrypto` is created by `init.sql`; the admin-promotion SQL below relies on its `digest()` function.

### Seed rows

`init.sql` inserts two models (`ON CONFLICT DO NOTHING`):

| name      | version | worker_name      | protocol | endpoint                           | supports_streaming |
| --------- | ------- | ---------------- | -------- | ---------------------------------- | ------------------ |
| `doc-ocr` | `v1`    | `doc-ocr-worker` | `http`   | `http://doc-ocr-worker:8001/infer` | false              |
| `asr`     | `v1`    | `asr-worker`     | `http`   | `http://asr-worker:8002/infer`     | true               |

These endpoints are Compose service names. In Kubernetes the same names resolve inside the `modelmesh` namespace.

### When the schema is applied

- **Compose:** `db/init.sql` is mounted into `/docker-entrypoint-initdb.d/` and runs **only when the Postgres volume is empty**. An existing volume keeps its old schema; apply the files in `migrations/` by hand. They are not mounted.
- **Kubernetes:** the same SQL is embedded in `k8s/postgres/init-configmap.yaml`. The two copies are identical today (checked), but they are two copies; regenerate the ConfigMap from `init.sql` whenever it changes.

### Migrations

| File                                                | Does                                                                     | Idempotent? |
| --------------------------------------------------- | ------------------------------------------------------------------------ | ----------- |
| `migrations/003_add_supports_streaming.sql`         | Adds `supports_streaming`; sets it true for `asr` v1                     | Yes         |
| `migrations/004_canary_percent_and_constraints.sql` | Adds `canary_percent NOT NULL DEFAULT 0` and the three CHECK constraints | Yes         |

Verified: applying both on top of a database built from `init.sql` produced only "already exists, skipping" notices and no errors. A database that predates migration 004 makes `GET /v1/models` fail with `KeyError: 'canary_percent'`.

### Connection settings

`DATABASE_URL` (default `postgresql://user:pass@localhost/modelmesh`). The Compose file uses `postgresql://user:pass@postgres/modelmesh` with the password in plain text. Kubernetes reads `db-secret` (`url` and `postgres-password`).

### Making a key an administrator

There is **no API** for this. Admin keys are created with SQL (shown in the setup guide):

```sql
UPDATE api_keys SET is_admin = TRUE
WHERE key_hash = encode(digest('<the raw API key>', 'sha256'), 'hex');
```

### Where schema and code disagree

- `status = 'deleted'` is allowed by the constraint, but no code path sets it. `DELETE /v1/models/{id}` sets `disabled`.
- `revoked` is read by key lookup, but nothing sets it. There is no endpoint to list or revoke keys.
- `requests` is dead schema.

## Redis

One Redis instance serves the queue, the job state and the rate limiter. Gateway code uses a client with `decode_responses=True`; workers use `decode_responses=False` so payload bytes round-trip.

### Keys

| Key                           | Written by                             | Lifetime                                          | Content                                                                                                                                             |
| ----------------------------- | -------------------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inference_jobs` (stream)     | Gateway; workers on retry              | Trimmed to about `STREAM_MAXLEN` (default 10,000) | One entry per job attempt: `job_id`, `model_id`, `model_name`, `model_version`, `worker_name`, `api_key_id`, `status`, and `retry_count` on retries |
| `inference_jobs_dlq` (stream) | Workers                                | Trimmed to about 1,000                            | `job_id`, `reason`, `elapsed_ms`                                                                                                                    |
| `payload:{job_id}`            | Gateway                                | `PAYLOAD_TTL_SECONDS` (default 3600)              | The uploaded bytes                                                                                                                                  |
| `job:{job_id}:owner`          | Gateway                                | 7200 s (fixed)                                    | The owning **user** ID                                                                                                                              |
| `job:{job_id}:status`         | Gateway (queued); workers (processing) | 3600 s (fixed); **deleted** when the job finishes | `{job_id, status, model_name, model_version}`                                                                                                       |
| `result:{job_id}`             | Workers                                | 7200 s (fixed)                                    | `{job_id, status: success, result}` or `{job_id, status: failed, error}`                                                                            |
| `rl:{api_key_id}`             | Gateway                                | 60 s window                                       | Integer request count                                                                                                                               |

Consumer groups are named `{WORKER_NAME}_group` (for example `doc-ocr-worker_group`). A group is created on first start with ID `0`, so a brand-new group reads the whole existing stream.

### What actually remains after a job (verified)

Measured with the OCR consumer logic against a real Redis, one 1 MB job:

| After enqueue                                                            | After success                                                                                                                                               |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `payload` TTL 3600; `owner` TTL 7200; `status` TTL 3600; stream length 1 | `payload` **still present** (TTL 3600); `owner` present (7200); `status` **deleted**; `result` TTL 7200; stream entry **still there** (length 1, pending 0) |

So:

- A finished job's payload occupies Redis for up to an hour. Acknowledging a stream entry does not delete it; only `MAXLEN` trimming does.
- A job that fails after all retries leaves **four** stream entries (the original plus one per retry), each acknowledged, plus one DLQ entry and one `result` key.
- Everything about a job disappears after about one to two hours; polling afterwards returns `404`.

### Memory limits and what a full Redis looks like (verified)

The Kubernetes Redis runs with `--maxmemory 200mb --maxmemory-policy noeviction` and **no volume**. With those settings (Redis 7.0.15), sending maximum-size (10 MiB) uploads back to back:

- the first **15** succeeded;
- the **16th returned HTTP 500 "Internal Server Error"**: Redis refused the write and the gateway does not catch the error;
- `/ready` still returned **200** because the readiness check only runs `PING`.

Memory use at that point was about 181 MB. Smaller uploads fit in proportion. Because payloads linger for an hour, this limit is reachable by a single client sending large files. Restarting the Redis pod empties the queue, results and payloads.

### Stream trimming

`XADD ... MAXLEN ~ N` is approximate. A test with `STREAM_MAXLEN=100` ended with 116 entries (reported in the Phase 9 completion report). Trimming can also remove **unread** entries if a worker falls more than `STREAM_MAXLEN` behind; the consumer logs a warning when the stream passes 80% of the limit.

### Persistence

Compose keeps a `redisdata` volume. Kubernetes has none. Jobs in flight are not durable in Kubernetes by design of the manifests.

## ClickHouse

### Table

`clickhouse/init.sql` (marked in its header as **reconstructed** from the insert statements and documented queries; verify with `SHOW CREATE TABLE` against a live table):

```sql
CREATE TABLE IF NOT EXISTS request_metrics (
    timestamp     DateTime DEFAULT now(),
    request_id    String,
    model_id      String,
    model_name    String,
    model_version String,
    worker_name   String,
    status        String,      -- 'success' or 'error'
    latency_ms    UInt32
) ENGINE = MergeTree
ORDER BY (model_name, model_version, timestamp);
```

No partitioning and no TTL, so the table grows without bound.

### What writes to it

Both Redis workers, once per job, on the **final** outcome only. A retry that will continue writes nothing. `latency_ms` is the duration of the last attempt, not including earlier attempts or backoff sleeps.

Not measured: direct WebSocket inference, and jobs that never finish.

The writer:

- is disabled when `CLICKHOUSE_HOST` is unset;
- creates its client lazily with a 2 s connect and 5 s send/receive timeout;
- inserts one row per call, off the event loop;
- on any failure drops the client and skips attempts for **60 seconds**; rows for jobs finishing in that window are lost, not buffered;
- logs at most one warning per back-off window.

Per-process back-off state means each worker process decides independently.

### Deploying it (Kubernetes only)

The manifests start the server and a Service but **do not apply the DDL and do not mount a volume**. Apply the table by hand after the pod is available:

```bash
kubectl exec -i -n modelmesh deployment/clickhouse -- clickhouse-client --multiquery < clickhouse/init.sql
```

The development image runs with `CLICKHOUSE_SKIP_USER_SETUP=1`, which removes the default-user password. That is not production-safe. Compose has no ClickHouse service, so metrics are off there.

### Useful queries

Latency over successful jobs only:

```sql
SELECT model_name, model_version,
       count() AS successful_jobs,
       avg(latency_ms) AS avg_latency_ms,
       quantile(0.95)(latency_ms) AS p95_latency_ms
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR AND status = 'success'
GROUP BY model_name, model_version;
```

Error rate over all final rows (errors stay in the denominator):

```sql
SELECT model_name, model_version,
       countIf(status = 'error') / count() AS error_rate
FROM request_metrics
WHERE timestamp > now() - INTERVAL 1 HOUR
GROUP BY model_name, model_version;
```

The canary guard runs a parameterised version of the second query; see [Registry and canary](registry-and-canary.md#the-rollback-guard).
