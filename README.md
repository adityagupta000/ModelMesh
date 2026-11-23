# ModelMesh

A multi-model inference gateway. Clients authenticate, name a model, and either submit an **asynchronous job** (REST) or send a request over a **WebSocket**. The gateway looks the model up in a Postgres **registry** on every request, so adding, disabling, re-pointing or canarying a model is a data change.

It ships with two example workers, **`doc-ocr`** (EasyOCR) and **`asr`** (Whisper `tiny`), runs on Docker Compose or Kubernetes (minikube), and includes a Redis-Streams job queue with retries and crash recovery, per-key rate limiting, best-effort ClickHouse metrics, and an optional canary rollback guard.

> **Status: a development implementation, not production-ready.** The core path is tested (102 tests pass, reproduced) and the project is candid about its gaps. But there is no TLS, workers and Redis are unauthenticated, registration is open, a full Redis returns bare HTTP 500s while `/ready` stays green, and uploads linger in Redis for an hour. Read [Status and limitations](docs/status-and-limitations.md) and [Security](docs/security.md) before exposing it.

## What it does

- **REST jobs.** `POST /v1/infer/{model}` stores the upload, queues a job, and returns a `job_id`; `GET /v1/jobs/{id}` returns the result. Jobs are private to the user who created them.
- **WebSocket inference.** `/v1/ws/infer/{model}` sends one whole file and gets the result back on the same socket, calling the worker over HTTP or gRPC as the registry says.
- **Registry-driven routing.** Name, version, worker, protocol, endpoint, status and canary percentage live in Postgres.
- **Canary routing** on the REST path by version number, with a CLI guard that can reset a canary to 0%.
- **Reliable workers.** Retries with 2, 4, 8 second backoff, a dead-letter stream, and recovery of jobs left by a crashed worker. Permanent input errors (bad image, bad audio) fail immediately with the real error.
- **Auth.** Users with bcrypt passwords, JWTs for creating API keys, hashed API keys, per-key fixed-window rate limits, admin keys for registry changes.
- **Best-effort metrics.** One ClickHouse row per finished job (Kubernetes, once the table is created).

## What it does not do

- **No TLS, Ingress, autoscaling, Prometheus, Grafana, or `/metrics`.** None are built.
- **No true streaming.** The "streaming" WebSocket sends the whole file as one message.
- **No canary, retries, queueing or metrics on the WebSocket path.**
- **No durable Redis on Kubernetes**, no job replay tool, no multi-tenancy, no result cache.
- **No end-to-end test and no CI pipeline.**
- The Kafka code is **dormant** and has drifted from the Redis path.

## How it fits together

```mermaid
flowchart LR
    C["Client"] -->|"REST job"| G["Gateway<br/>FastAPI :8000"]
    C -->|"WebSocket"| G
    G --> PG[("Postgres<br/>users, keys, registry")]
    G --> RD[("Redis<br/>job stream, payloads,<br/>results, rate limits")]
    RD --> W1["doc-ocr worker<br/>HTTP, gRPC, consumer"]
    RD --> W2["asr worker<br/>HTTP, gRPC, consumer"]
    G -.->|"WebSocket path:<br/>direct call"| W1
    G -.-> W2
    W1 -.->|"best effort"| CH[("ClickHouse<br/>Kubernetes only")]
    W2 -.-> CH
```

Details: [Architecture](docs/architecture.md).

## Quick start

Requires Docker with Compose (about 10 GB of disk; the **first build is slow**, about 51 minutes on the author's machine).

```bash
cp .env.example .env        # set JWT_SECRET to a long random value
docker compose up --build -d
curl -s http://localhost:8000/ready          # {"status":"ready"}
```

Then register, log in, create an API key and submit a job; the step-by-step commands, sample files, admin setup and the Kubernetes path are in [Getting started](docs/getting-started.md).

## API at a glance

| Endpoint                                            | Auth                | Purpose                               |
| --------------------------------------------------- | ------------------- | ------------------------------------- |
| `POST /v1/auth/register`, `/login`, `/api-keys`     | none, none, JWT     | Accounts and API keys                 |
| `GET /v1/models`, `/v1/models/{name}`               | none                | Public model listing (routing hidden) |
| `POST /v1/models`, `PATCH`/`DELETE /v1/models/{id}` | admin key           | Manage the registry                   |
| `POST /v1/infer/{model}`                            | API key             | Submit a job                          |
| `GET /v1/jobs/{id}`                                 | API key (same user) | Poll a job                            |
| `WS /v1/ws/infer/{model}`                           | API key in query    | Direct inference                      |
| `GET /health`, `/ready`                             | none                | Liveness, readiness                   |

Full contract with captured responses: [API](docs/api.md).

## Know this before you rely on it

Measured on a real Postgres 16 and Redis 7 during this documentation review:

- **A full Redis breaks uploads with HTTP 500.** With the Kubernetes setting (200 MB, no eviction), the 16th consecutive 10 MiB upload failed. `/ready` kept answering 200.
- **WebSocket errors leak the internal worker URL** that `/v1/models` is built to hide.
- **Finished jobs leave their upload in Redis for an hour.**
- **A job for a model with no running worker is never processed and never fails.** It shows `queued` until it expires.
- **The canary guard can roll back a healthy canary** right after it is enabled; wait one full window before running it with `--apply`.
- The suite needs a real Postgres and Redis and **leaves rows behind**; use a scratch database.

## Repository layout

```
gateway/          FastAPI gateway: main.py, auth, registry, job_queue, rate_limit,
                  grpc_client, canary_guard.py (CLI), kafka_queue.py (dormant)
workers/
  doc_ocr/        EasyOCR worker: HTTP, gRPC, Redis consumer, metrics
  asr/            Whisper worker: HTTP, gRPC (with PredictStream), consumer, metrics
db/init.sql       Postgres schema and seed models
migrations/       Idempotent schema changes (003, 004)
clickhouse/       Metrics table DDL (reconstructed; apply by hand)
k8s/              Namespace, Postgres, Redis, ClickHouse, gateway, workers
docker-compose.yml, docker-compose.test.yml
scripts/smoke_test.sh
test_ws_client.py, test_ws_client.html, ws_latency_compare.py
load-test.yml     Artillery scenario
tests/            102 pytest tests (real Postgres and Redis required)
docs/             Documentation (below)
```

Not in the reviewed export but referenced by the project: `inference.proto`, two Kubernetes Secret manifests, `.env.example`, `fixtures/`, `load-test-vars.csv`.

## Documentation

| Document                                                 | Read it for                                                                   |
| -------------------------------------------------------- | ----------------------------------------------------------------------------- |
| [Getting started](docs/getting-started.md)               | Prerequisites, Compose and minikube, first requests, admin setup, smoke test  |
| [Architecture](docs/architecture.md)                     | Components, both request paths, process model, decisions                      |
| [API](docs/api.md)                                       | Every endpoint and WebSocket message, errors, limits, captured examples       |
| [Queue and workers](docs/queue-and-workers.md)           | Job lifecycle, retries, dead letters, crash recovery, the two workers, gRPC   |
| [Registry and canary](docs/registry-and-canary.md)       | Version selection, canary, adding models, the rollback guard                  |
| [Data stores](docs/data-stores.md)                       | Postgres schema, Redis keys and lifetimes, ClickHouse                         |
| [Operations](docs/operations.md)                         | Configuration reference, sizing, monitoring, troubleshooting                  |
| [Security](docs/security.md)                             | Controls, gaps, what an operator must add                                     |
| [Testing](docs/testing.md)                               | What the 102 tests cover and miss, how to run them, scripts, recorded numbers |
| [Status and limitations](docs/status-and-limitations.md) | Verdict, verified facts, known issues, history, open work                     |

## Tests

```bash
export TEST_DATABASE_URL=postgresql://user:pass@localhost/modelmesh_test
export TEST_REDIS_URL=redis://localhost:6379/1
pip install -r tests/requirements.txt
pytest -q                     # 102 passed, 8 warnings; run it twice
```

Setup of the scratch database and Redis is in [Testing](docs/testing.md#running-the-tests).

## License

No license file or license statement was present in the reviewed export. Add one before distributing the code.
