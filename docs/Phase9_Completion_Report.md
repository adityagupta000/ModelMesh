# Phase 9 completion report

## Scope

Phase 9 queue and worker reliability work is complete.

## Implemented

- REST, WebSocket, and gRPC upload limits with a 10 MB default.
- Payload keys with configurable TTL and stream/DLQ trimming.
- Permanent versus transient worker failures with real error text.
- Stale pending-job reclaim and unique hostname-based consumer names.
- Environment-configurable worker names.
- Worker process supervision and Kubernetes HTTP probes.
- ASR startup preload with a lock.
- Configurable gRPC worker threads: 10 locally, 3 in Kubernetes.
- Environment variable and deployment-order documentation.
- Redis 200 MB no-eviction guard in Kubernetes.

## Verification

- Complete pytest suite: `54 passed, 6 warnings`.
- 20 MB REST upload: `413`.
- Payload key sample: TTL `3402` seconds.
- Stream test with `STREAM_MAXLEN=100`: 116 entries, consistent with approximate trimming.
- Dummy model disabled after verification.
- Container sub-process kill: doc-ocr container restarted; `RestartCount` became `1`.
- Revoked/health behavior remained green.
- Both worker entrypoints contain no CR characters.
- Consumer diff contains only worker name, inference function, and doc-ocr metric calls.
- Final Docker memory figures: gateway 66.53 MiB, doc-ocr 1.185 GiB, ASR 1.084 GiB, Redis 19.96 MiB, Postgres 31.82 MiB.
- Kubernetes live cluster verification remains unavailable.

## Live behavior caveat

Invalid OCR jobs now set the real status message `Invalid image data` and no longer retry as generic `Processing failed`. A direct live timing probe reached `failed` with the real message. The current Compose timing also includes the existing synchronous ClickHouse metric retry logging, which is explicitly left for Phase 10. The unit tests verify permanent failures perform no sleep and expired payloads return `payload expired`.
