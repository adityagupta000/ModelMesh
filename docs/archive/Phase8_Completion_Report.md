# Phase 8 completion report

> **Historical phase result.** The counts and smoke measurements below are from Phase 8 and were superseded by later phases. The latest recorded review-follow-up full-suite result is 102 passed, 8 warnings; see the [Phase 11 completion report](Phase11_Completion_Report.md).

## Scope

Phase 8 gateway validation, auth, routing, rate limiting, canary, readiness, secret hygiene, and documentation work is complete.

## Verification

- Complete pytest suite: `43 passed, 6 warnings`.
- Registry PATCH validation: invalid protocol, status, and canary percent return 422.
- Registration validation: invalid version and passwords shorter than 8 characters return 422.
- Existing short passwords remain valid for login.
- Shared key lookup resolves valid keys and rejects revoked keys.
- HTTP routing uses `application/octet-stream` for OCR and ASR payloads.
- The document OCR HTTP handler no longer rejects by content type and translates invalid-image decode failures to 400.
- Fixed-window rate limiting uses atomic Redis pipeline operations and creates a bounded 60-second TTL.
- Canary routing compares version numbers numerically, supports boundary equality, explicit version bypass, and one active version.
- Models with no active version return 503; unknown model names return 404.
- `/ready` returns 503 for Redis failure and 200 when Redis and Postgres are available.
- The JWT placeholder secret produces a startup warning instead of preventing local startup.
- Kubernetes PostgreSQL password now comes from `db-secret/postgres-password`.
- At Phase 8, all 16 Kubernetes YAML files parsed locally; live cluster rendering was unavailable at that time. A later Minikube follow-up is recorded in the [Phase 9 completion report](Phase9_Completion_Report.md).
- Grep found no hardcoded `"ocr" in` or `model_name == "asr"` branches and no revoked-key reimplementation in the WebSocket handler.

## Live smoke evidence

- Two live smoke runs returned `SMOKE: PASS` with identical PASS/FAIL check identities.
- Bad protocol PATCH returned 422.
- Redis-down `/ready` returned 503, then 200 after Redis restarted.
- A valid key worked over WebSocket before revocation.
- The revoked key was rejected over WebSocket with close code 4401.
- Latency gate: doc-ocr p95 18.493 seconds and ASR p95 2.268 seconds, compared with baseline p95 values of 16.665 seconds and 2.142 seconds. Both remain within 25%.
