# Testing

What the 102 tests cover, what they do not, what was reproduced for this documentation, and how to use the smoke, load and latency scripts.

## Verification record

Reproduced during this review (Ubuntu 24.04, Python 3.12.3, **Postgres 16** and **Redis 7.0.15** installed locally, the pinned `tests/requirements.txt` installed in a fresh virtual environment, schema built from `db/init.sql` plus both migrations, `TEST_DATABASE_URL` pointing at a scratch database and `TEST_REDIS_URL` at Redis database 1):

| Check                                                                                                                                            | Result                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `pytest -q`, first run                                                                                                                           | **102 passed, 8 warnings** in about 3 s                                                                       |
| `pytest -q`, second run                                                                                                                          | **102 passed, 8 warnings** (the suite is repeatable)                                                          |
| Migrations applied on top of `init.sql`                                                                                                          | Only "already exists, skipping" notices (idempotent)                                                          |
| Kubernetes ConfigMap SQL vs `db/init.sql`                                                                                                        | Identical                                                                                                     |
| All Python files compile; all 14 Kubernetes manifests, both Compose files and the load-test file parse as YAML; all shell scripts pass `bash -n` | Yes                                                                                                           |
| `canary_guard.decide()` exercised with defaults                                                                                                  | Table in [Registry and canary](registry-and-canary.md#what-the-decision-function-does-verified-by-running-it) |

**Caveat.** `inference.proto` was not in the reviewed export, so I wrote a minimal stand-in from how the code uses the messages (field numbers are arbitrary). The tests passed with it, which shows the code and tests are consistent with each other, but the real proto file was not checked.

**Not run:** the Docker images, Docker Compose, Kubernetes, ClickHouse, the real EasyOCR and Whisper models, the smoke test, the load test, and anything needing GPUs. Everything recorded below as "reported" comes from the project's own completion reports, not from this review.

The 8 warnings are FastAPI deprecations for `@app.on_event("startup"/"shutdown")` in the gateway and the ASR worker, plus protobuf deprecation notices.

## Running the tests

The suite needs a **real Postgres and a real Redis**. Use a scratch database: the tests leave rows behind.

```bash
# 1. Start Postgres and Redis, published on localhost (a Compose overlay does this)
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d

# 2. Create the scratch database and apply the schema
docker compose exec -T postgres psql -U user -d postgres -c "CREATE DATABASE modelmesh_test;"
docker compose exec -T postgres psql -U user -d modelmesh_test < db/init.sql
for f in migrations/*.sql; do
  docker compose exec -T postgres psql -U user -d modelmesh_test < "$f"
done

# 3. Python environment (Python 3.10+, on the Linux filesystem)
python -m venv ~/.venvs/modelmesh && . ~/.venvs/modelmesh/bin/activate
pip install -r tests/requirements.txt

# 4. Run. The suite uses Redis database 1.
export TEST_DATABASE_URL=postgresql://user:pass@localhost/modelmesh_test
export TEST_REDIS_URL=redis://localhost:6379/1
pytest -q          # then run it a second time to confirm it is repeatable
```

`pytest.ini` sets `asyncio_mode = auto` and `testpaths = tests`, so the scripts in the repository root (`test_ws_client.py`, `ws_latency_compare.py`) are not collected.

`tests/conftest.py` generates the protobuf modules into a temporary directory from `inference.proto` at import time (so the proto file must be present), and flushes Redis database 1 before **every** test. It refuses to run that flush against any other Redis database number.

## What the suite covers

| File                           |   Tests | Focus                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------------ | ------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_consumer_reliability.py` |      21 | Parametrized over both workers. Permanent vs transient errors, retry then success, real error in the failed status, expired payload, exactly one metric per job, reclaim of stale entries, maintenance driven by elapsed time, and one test with a **real Redis**: a pending job from a "ghost" consumer is reclaimed                                      |
| `test_gateway.py`              |      20 | Atomic rate limit, canary selection (numeric highest and lowest, 0% routes stable, boundary roll, single active version, explicit version bypass), `/ready` behaviour, upload at and over the limit, log redaction of `api_key=`, public model reads hide routing, job lookup by authenticated user, octet-stream worker call, one WebSocket oversize test |
| `test_registry.py`             |      15 | Real-database registry: lookup, listing, disabled and unknown models, version and value validation, duplicate registration, admin-only registration, disable                                                                                                                                                                                               |
| `test_auth.py`                 |      13 | Register, login, keys, revoked keys, short passwords, rate-limit enforcement, key limit bounds                                                                                                                                                                                                                                                             |
| `test_canary_guard.py`         |      11 | The pure `decide()` function over many cases, numeric version selection, zero-percent pairs                                                                                                                                                                                                                                                                |
| `test_metrics.py`              |       6 | ClickHouse writer: configured host and port, disabled when unset, one warning per back-off window                                                                                                                                                                                                                                                          |
| `test_asr.py`                  |       5 | Transcription, empty audio, invalid audio becomes a permanent input error, HTTP 400                                                                                                                                                                                                                                                                        |
| `test_doc_ocr.py`              |       4 | Text and confidence, invalid bytes, octet-stream accepted, HTTP 400                                                                                                                                                                                                                                                                                        |
| `test_queue.py`                |       3 | Payload stored before the stream entry, job visible only to its owner                                                                                                                                                                                                                                                                                      |
| `test_ws_client.py`            |       3 | The client script exits with the right status                                                                                                                                                                                                                                                                                                              |
| `test_grpc_transport.py`       |       1 | A 6 MiB WAV payload crosses a real gRPC server and client                                                                                                                                                                                                                                                                                                  |
| **Total**                      | **102** |                                                                                                                                                                                                                                                                                                                                                            |

Counts are the pytest-collected numbers, so parametrized tests count once per case.

### How the tests are built

- **Heavy dependencies are stubbed.** The worker tests load `consumer.py` and the HTTP apps through `tests/helpers.py` (`load_module_from_path`) with stand-ins for `inference` and `metrics`, so no model weights are needed. This is also why the dormant Kafka modules are not touched.
- **Real services where it matters:** registry, auth, rate limiting and the ghost-consumer reclaim use the real Postgres and Redis. Most consumer tests use an `AsyncMock` Redis.
- Test dependency versions were aligned with the worker images (NumPy 1.26.4, OpenCV headless 4.10.0.84) after an earlier mismatch.

## What is not tested

- **No end-to-end test through real workers.** No test sends a file through the gateway, real Redis, a running worker process and back.
- **The WebSocket path is barely covered.** Only the oversized-payload case is tested, with a stub socket. Authentication, the success path, the gRPC streaming path and the error path (including the internal-URL leak described in [Security](security.md)) have no tests.
- **`PredictStream` has no test.** The only gRPC test is a unary 6 MiB call.
- **ClickHouse is never contacted.** Metric tests use fakes.
- **No coverage of the Kafka modules, the Dockerfiles, the entrypoints, the Kubernetes manifests, or the load test.**
- **No test of `GET /v1/jobs` over HTTP**, of JWT issuance of API keys through the endpoint, or of the "full Redis" failure.
- **Real model behaviour** (OCR accuracy, transcript quality) is not tested; the fixtures are a rendered line of text and two seconds of silence.
- No coverage tool, linter, type checker, security scan or CI pipeline is configured.

An earlier mutation check (reported in Phase 7) confirmed two key tests fail when the behaviour is removed: dropping the admin check broke the admin test, and removing the 429 branch broke the rate-limit test.

## Test hygiene

Each full run leaves rows in the test database: **one disabled `test-ocr-<hex>` model, five users and four API keys** (verified over three runs). Session fixtures create users and keys without cleanup. This is why the instructions say to use a scratch database. Reset it by recreating the database, or just delete the rows.

Redis is flushed before every test, so never point `TEST_REDIS_URL` at a Redis you care about; the guard only enforces database number 1, not the host.

## The other scripts

### `scripts/smoke_test.sh`

Runs against a live Compose stack. Checks and requirements are in [Getting started](getting-started.md#smoke-test). Things to know:

- It exits 1 if any check fails; the `bad-upload` check is informational and never fails the run.
- The admin check uses `docker compose exec`, so it does not work against Kubernetes.
- `--latency N` runs N **sequential** jobs per model and polls every 2 seconds. The first poll happens immediately, then it sleeps 2 s between polls, so a job that takes a fraction of a second is recorded at about 2 seconds. With N = 5, the 95th percentile is simply the slowest of five.
- It depends on `fixtures/`, which is not in the repository.

### Reported smoke and latency results (author-reported, not reproduced)

| Point                   | OCR p95 (s) | ASR p95 (s) | Notes                                                                                                                          |
| ----------------------- | ----------: | ----------: | ------------------------------------------------------------------------------------------------------------------------------ |
| Baseline (Phase 0 to 6) |      16.665 |       2.142 | Before reliability and metrics fixes; Compose timing included synchronous ClickHouse metric retry logging, removed in Phase 10 |
| Phase 8                 |      18.493 |       2.268 |                                                                                                                                |
| Phase 10                |       2.118 |       2.120 | `--latency 5`                                                                                                                  |
| Phase 11                |       2.154 |       2.118 | `--latency 5`                                                                                                                  |

Do not read the later numbers as a performance improvement: different sample sizes (20 vs 5), one tiny fixture each, a 2-second polling floor, and a baseline that included a since-fixed overhead. They show only that nothing got worse than the 25% regression gate allowed.

Other reported figures: an invalid image failed in about 92 seconds before the reliability work and about 3 seconds after; a successful OCR job took 2.04 s and an ASR job 4.06 s in the first baseline.

### `load-test.yml` (Artillery)

Phases of 5, 20 and 100 requests per second for 30, 30 and 15 seconds, against OCR, ASR and `GET /v1/models`. Setup is in [Getting started](getting-started.md#load-test). The recorded baseline measured **enqueue latency** for accepted requests only (p50 2 ms, p95 4 ms, p99 29 ms) and noted that about two thirds of the requests in that run were rejected with `401` or `422`; it is not a throughput or success-rate figure.

### `ws_latency_compare.py`

`python ws_latency_compare.py <admin_key> <model> <file> [n]` sends `n` sequential WebSocket requests with the model registered over HTTP and then over gRPC, and prints min, p50, p95, p99, max and mean. Kubernetes only (it writes in-cluster DNS names into the registry), and it **leaves the model registered as gRPC** afterwards. Reported results: [Registry and canary](registry-and-canary.md#switching-a-models-protocol-websocket-path-only).

### `test_ws_client.py` and `test_ws_client.html`

Reference WebSocket clients, described in [API](api.md#websocket-v1wsinfermodel_name).

## History of the suite

Full-suite counts reported at the end of each phase: 20 passed and 1 expected failure (Phase 7), 43 (Phase 8), 54 (Phase 9), 64 (Phase 10), 75 (Phase 11), **102** after the review follow-up (reproduced here). The run before any hardening had 20 failing tests and 1 passing, caused by an asyncio loop mismatch, tests assuming a running database backend, and a module-name collision.
