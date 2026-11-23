# API

The gateway's HTTP and WebSocket contract. Every example below was **captured from a running gateway** against a real Postgres and Redis during this review. Identifiers are shortened (`…`) and tokens truncated; nothing else is edited. FastAPI also serves interactive documentation at `/docs`, `/redoc` and `/openapi.json`, **without authentication**.

| Endpoint                                  | Auth            | Purpose                                |
| ----------------------------------------- | --------------- | -------------------------------------- |
| `POST /v1/auth/register`                  | none            | Create a user                          |
| `POST /v1/auth/login`                     | none            | Get a JWT                              |
| `POST /v1/auth/api-keys`                  | JWT             | Create an API key                      |
| `GET /v1/models`, `GET /v1/models/{name}` | **none**        | List or read models (internals hidden) |
| `POST /v1/models`                         | admin API key   | Register a model version               |
| `PATCH /v1/models/{model_id}`             | admin API key   | Update a model version                 |
| `DELETE /v1/models/{model_id}`            | admin API key   | Disable a model version                |
| `POST /v1/infer/{model_name}`             | API key         | Submit an asynchronous job             |
| `GET /v1/jobs/{job_id}`                   | API key         | Poll a job                             |
| `WS /v1/ws/infer/{model_name}`            | API key (query) | Direct inference over a WebSocket      |
| `GET /health`, `GET /ready`               | none            | Liveness and readiness                 |

## Conventions

- Base URL `http://localhost:8000`. There is no TLS, no CORS handling (a browser preflight returns `405`), and no `/metrics` endpoint.
- Request and response bodies are JSON, except inference uploads, which are `multipart/form-data` with one field named `file`.
- Errors use FastAPI's shape: `{ "detail": "<message>" }`, or for validation errors (`422`) `{ "detail": [ { "type", "loc", "msg", … } ] }`.
- **Two credentials:**
  - A **JWT** (24 hours, HS256) from `/v1/auth/login`. It is accepted **only** by `POST /v1/auth/api-keys`.
  - An **API key** (random, shown once) sent as `Authorization: Bearer <key>`. It is accepted by everything else. Admin routes need a key with `is_admin = true`, which can only be set in the database ([Data stores](data-stores.md#making-a-key-an-administrator)).
- The app sets no security headers (no `nosniff`, framing, CSP or HSTS).

## Authentication

### `POST /v1/auth/register`

```json
{ "email": "user@example.com", "password": "at-least-8-chars" }
```

`200` → `{ "message": "User registered" }`. A duplicate email returns `400 {"detail": "Email already registered"}`. A password under 8 characters returns `422` (`"String should have at least 8 characters"`). The limit applies to new registrations only; older short passwords still work for login. There is no email verification and no rate limit on this endpoint.

### `POST /v1/auth/login`

Same body. `200` → `{ "access_token": "eyJhbGciOiJI…", "token_type": "bearer" }`. Wrong credentials: `401 {"detail": "Invalid credentials"}`. Login is not rate limited.

### `POST /v1/auth/api-keys`

Send `Authorization: Bearer <JWT>` and optionally `{ "rate_limit_per_min": 100 }` (default 60, allowed 1 to 1000).

`200` → `{ "key": "iMhjy9…", "rate_limit_per_min": 100 }`. **The raw key is returned once**; only its SHA-256 is stored. A value outside 1 to 1000 returns `422` (`"Input should be less than or equal to 1000"`). A missing or invalid token returns `401 {"detail": "Missing or invalid Authorization header"}`.

A user may create any number of keys, each with its own limit, so the per-key limit is not a per-user cap. There are no endpoints to list or revoke keys.

## Models

### `GET /v1/models` and `GET /v1/models/{name}?version=v1`

No authentication. The response **omits** `worker_name` and `endpoint` (the internal routing) but includes the model `id` and `canary_percent`. `GET /v1/models/{name}` defaults to `version=v1`.

```json
{
  "id": "…",
  "name": "asr",
  "version": "v1",
  "protocol": "http",
  "status": "active",
  "supports_streaming": true,
  "canary_percent": 0,
  "description": "Whisper-tiny speech-to-text"
}
```

Unknown: `404 {"detail": "Unknown model"}`. Disabled models are still listed, with `"status": "disabled"`; deleted models are not.

### `POST /v1/models` (admin)

```json
{
  "name": "doc-ocr",
  "version": "v2",
  "worker_name": "doc-ocr-worker-v2",
  "protocol": "http",
  "endpoint": "http://doc-ocr-worker-v2:8001/infer",
  "supports_streaming": false,
  "canary_percent": 0,
  "description": "candidate"
}
```

`200` → `{ "id": "…", "status": "registered" }`.

| Field            | Rule                                   |
| ---------------- | -------------------------------------- |
| `version`        | Must match `^v\d+$` (default `v1`)     |
| `protocol`       | `http` or `grpc`                       |
| `canary_percent` | 0 to 100                               |
| `endpoint`       | Any string; **not validated as a URL** |

Errors: `403 {"detail": "Admin key required"}` for a non-admin key; `409 {"detail": "Model doc-ocr:v2 already registered"}`; `422` for a bad version, protocol or percentage.

### `PATCH /v1/models/{model_id}` (admin)

Any of `worker_name`, `protocol`, `endpoint`, `status` (`active` or `disabled`), `supports_streaming`, `canary_percent`, `description`. `200` → `{ "status": "updated" }`. Invalid values return `422` (for example `"Input should be 'http' or 'grpc'"`). Null values are ignored, so a description cannot be cleared; `name` and `version` cannot be changed. An unknown ID returns `404`.

### `DELETE /v1/models/{model_id}` (admin)

Soft delete: sets `status` to `disabled` and returns `{ "status": "disabled" }`. The row stays readable. New requests get `503 {"detail": "Model is currently disabled"}`.

How a version is chosen when none is given is described in [Registry and canary](registry-and-canary.md#how-a-model-version-is-chosen-rest).

## Inference (asynchronous)

### `POST /v1/infer/{model_name}?version=v2`

`multipart/form-data`, field `file`. Requires an API key.

```http
POST /v1/infer/doc-ocr
Authorization: Bearer <api key>
```

`200`:

```json
{
  "job_id": "a583a59d-…",
  "status": "queued",
  "model": "doc-ocr",
  "version": "v1"
}
```

Order of checks (this matters for which error you see and what counts against your limit):

1. API key (`401`).
2. **Rate limit** (`429`). This is counted even if the request fails a later check.
3. Model lookup (`404`, `503`).
4. Upload read and size check (`413`).
5. Enqueue.

| Status | `detail`                                                                                                                                                              |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 401    | `Missing or invalid Authorization header` or `Invalid or revoked API key`                                                                                             |
| 404    | `Unknown model` (also for an unknown `?version=`)                                                                                                                     |
| 413    | `Upload exceeds maximum allowed size` (limit `MAX_UPLOAD_BYTES`, default 10 MiB; checked after the body is parsed)                                                    |
| 422    | Missing `file` field                                                                                                                                                  |
| 429    | `Rate limit exceeded` (no `Retry-After` or rate-limit headers)                                                                                                        |
| 503    | `Model is currently disabled`                                                                                                                                         |
| 500    | Unhandled error, for example **Redis refusing the write when it is full** (see [Data stores](data-stores.md#memory-limits-and-what-a-full-redis-looks-like-verified)) |

Pinning a version with `?version=` bypasses the canary split.

### `GET /v1/jobs/{job_id}`

Requires an API key belonging to the **same user** that created the job.

```json
{
  "job_id": "a583a59d-…",
  "status": "queued",
  "model_name": "doc-ocr",
  "model_version": "v1"
}
```

Later responses: `{ "job_id", "status": "processing" }` while a worker holds the job (only the initial `queued` record carries the model name and version), then `{ "job_id", "status": "success", "result": { … } }` or `{ "job_id", "status": "failed", "error": "…" }`. Result shapes per model are in [Queue and workers](queue-and-workers.md#the-workers).

A missing job, an expired job and **another user's job all return the same** `404 {"detail": "Job not found or expired"}`. Results are kept about two hours, other job state about one hour.

## WebSocket: `/v1/ws/infer/{model_name}`

Query parameters: `api_key` (required) and `version` (optional; **defaults to `v1`**, never uses the canary).

The server accepts the connection first, then validates. Flow:

1. Server sends `{ "status": "ready", "model": "doc-ocr", "protocol": "http" }`.
2. Client sends **one binary message** containing the whole file.
3. Server replies, then closes:

| Path                                   | Reply                                                                            |
| -------------------------------------- | -------------------------------------------------------------------------------- |
| HTTP worker, or gRPC without streaming | `{ "result": { … }, "is_final": true }`                                          |
| gRPC with `supports_streaming`         | One or more `{ "partial": "<text>", "is_final": <bool>, "confidence": <float> }` |

Rejections, each sent as `{ "error": "<message>" }` followed by a close (all verified):

| Case                             | Message                           | Close code          |
| -------------------------------- | --------------------------------- | ------------------- |
| No `api_key`                     | `Missing api_key query parameter` | 4401                |
| Bad or revoked key               | `Invalid API key`                 | 4401                |
| Unknown model or version         | `Unknown model`                   | 4404                |
| Model disabled                   | `Model is currently disabled`     | 4503                |
| Rate limited                     | `429: Rate limit exceeded`        | 1000 (normal close) |
| Message above `MAX_UPLOAD_BYTES` | none                              | 1009                |

Notes:

- **Credentials travel in the URL.** The gateway redacts `api_key=` in its own access logs, but proxies and browsers may not.
- **Unexpected errors are returned verbatim** as `{ "error": "<exception text>" }`. Verified: when an HTTP worker returns 500, the client receives `Server error '500 Internal Server Error' for url 'http://<internal worker address>/infer'`, which discloses the worker address that `/v1/models` deliberately hides.
- The WebSocket path is **not queued, not retried, not metered and does not use the canary.**
- "Streaming" means the gRPC `PredictStream` call with a single final chunk; see [Queue and workers](queue-and-workers.md#grpc-servers).
- A reference client is `test_ws_client.py` (`python test_ws_client.py <api_key> <model> <file>`); it exits non-zero on any error or timeout. `test_ws_client.html` is a browser version.

## Health

- `GET /health` → `200 {"status": "ok"}` always; it checks nothing.
- `GET /ready` → `200 {"status": "ready"}` after a `SELECT 1` on Postgres and a `PING` on Redis; otherwise `503` with `Not ready: Postgres unavailable` or `Not ready: Redis unavailable`. It does **not** detect a full Redis.

## Rate limiting

Per API key, a **fixed 60-second window** that starts at the key's first request and is not extended by later ones. The counter increments atomically (`SET … NX EX 60` then `INCR`, in one transaction). Exceeding `rate_limit_per_min` returns `429`. Notes:

- Rate-limited, unknown-model and oversize requests all still count.
- A burst at the end of one window and the start of the next can reach twice the limit.
- No `Retry-After` or `X-RateLimit-*` headers are sent.
- If Redis is down the check raises and the request returns `500` (there is no fail-open).
- Registration and login are not limited.

## Limits

| Limit                                             | Value                                                   |
| ------------------------------------------------- | ------------------------------------------------------- |
| Upload size (REST and WebSocket)                  | `MAX_UPLOAD_BYTES`, default 10,485,760                  |
| `rate_limit_per_min`                              | 1 to 1000 per key                                       |
| Password length                                   | at least 8 (new registrations)                          |
| JWT lifetime                                      | 24 hours                                                |
| Job state lifetime                                | about 1 hour (status, payload); 2 hours (result, owner) |
| gRPC call timeout                                 | 30 s (`Predict`), 60 s (`PredictStream`)                |
| HTTP call from gateway to worker (WebSocket path) | 30 s                                                    |

## Quirks worth knowing

- `GET /v1/models/{name}` and `PATCH/DELETE /v1/models/{model_id}` share a path shape. A model **ID** passed to `GET` is treated as a name and returns `404`.
- `GET /v1/models` is public and shows `canary_percent` and model IDs.
- An unauthenticated caller can reach `/docs`, `/openapi.json` and register accounts.
