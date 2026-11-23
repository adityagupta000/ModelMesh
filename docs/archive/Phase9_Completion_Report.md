# Phase 9 completion report

> **Historical phase result.** The counts and runtime measurements below are from Phase 9 and were superseded by later phases. The Phase 11-era full suite reported 75 passed, 6 warnings. Current review-follow-up verification is recorded below.

## Scope

Phase 9 queue and worker reliability work is complete.

## Implemented

- REST, WebSocket, and gRPC upload limits with a 10 MiB (10,485,760 bytes) default.
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
- Kubernetes was not exercised during the original Phase 9 run; see the later live-cluster results below.

## Live behavior caveat

Invalid OCR jobs now set the real status message `Invalid image data` and no longer retry as generic `Processing failed`. A direct live timing probe reached `failed` with the real message. At the time of the Phase 9 measurement, Compose timing also included synchronous ClickHouse metric retry logging, which was left for Phase 10. The unit tests verify permanent failures perform no sleep and expired payloads return `payload expired`.

## Review follow-up verification

The later review added focused evidence for the previously untested reliability branches:

- A local gRPC integration test sent a valid 6 MiB WAV through the configured client and server message limits.
- A Redis-backed ghost-consumer test left a job pending under the old consumer, reclaimed it under the worker consumer, and verified completion and ACK.
- Unit tests cover processing reclaimed entries, ACKing trimmed pending IDs, and closing an oversized non-streaming WebSocket message with code 1009.
- Invalid Whisper decode errors are now classified as permanent invalid input; the worker does not retry them and the HTTP worker returns 400.
- Current full-suite result: `102 passed, 8 warnings`.

The local Minikube follow-up completed the previously missing checks:

- All six application pods reached Ready. The `db-secret` contains the required `postgres-password` and `url` keys, and a gateway rolling restart completed successfully.
- A synthetic OCR sample completed through the gateway and detected `HELLO MODELMESH 12345`. The OCR container's measured cgroup peak was 2,503 MiB.
- The previous 1.5 GiB OCR limit and a temporary 2 GiB limit both caused startup OOM kills. The Kubernetes OCR limit is now 3 GiB; the worker became Ready and completed the sample with the full stack running.
- A stream entry left pending under a ghost consumer was reclaimed after restarting the OCR deployment, completed successfully, and ACKed. The pending count returned to zero, test Redis data was removed, and `RECLAIM_IDLE_MS` was restored to 300000.

The memory result covers the small synthetic sample, not large-document stress. Although Minikube was started with `--memory=4096`, the node reported 7.58 GiB allocatable, so this run does not establish a hard 4 GiB node-memory limit.
