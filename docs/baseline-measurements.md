# ModelMesh Latency Baseline Measurements

## Phase 1/2 Baseline (HTTP gateway → Redis Streams queue)

Date: 2026-07-06

### Gateway response time (job acceptance, 2xx only)

| Metric       | Value |
| ------------ | ----- |
| p50 (median) | 2 ms  |
| p95          | 4 ms  |
| p99          | 29 ms |

### Notes

- These numbers measure gateway enqueue latency, not end-to-end inference latency
- ~67% of requests returned 401/422 (Artillery multipart file upload limitation)
- 730 successful requests out of 2250 total
- Async queue design: gateway returns job_id immediately, inference happens in background
- Test: Artillery 5→20→100 req/sec ramp, OCR + ASR + list-models mixed scenarios

### Phase 3 comparison target

After adding gRPC for worker communication, re-run this same test and compare
p95 against the 4ms baseline above.

## Phase 3 Update: Artillery re-run (2026-07-07)

Re-ran the same Artillery test after implementing gRPC. Results:

| Metric | Phase 1/2 | Phase 3 re-run |
| ------ | --------- | -------------- |
| p50    | 2 ms      | 3 ms           |
| p95    | 4 ms      | 7.9 ms         |
| p99    | 29 ms     | 32.1 ms        |

**Finding**: No meaningful difference (within normal variance). This is expected,
not a failure of gRPC — the `/v1/infer/{model_name}` endpoint (Phase 2's async
job queue) never reads the registry's `protocol` field. Workers call their
inference functions directly in `consumer.py`, bypassing `route_request()` and
the gRPC client entirely. Protocol dispatch (`http` vs `grpc`) only happens in
the WebSocket path (`/v1/ws/infer/{model_name}`), which Artillery's core HTTP
engine doesn't exercise.

**Honest conclusion**: Artillery here measures gateway enqueue latency, which
is protocol-agnostic by design. To actually measure gRPC vs HTTP inference
latency, the WebSocket path was timed directly (see below) rather than
relying on Artillery.

## Phase 3 Update: HTTP vs gRPC comparison (WebSocket path), 2026-07-07

Method: 20 sequential requests per protocol per model, full round-trip
(connect → send file → receive final result), via a custom script
(`ws_latency_compare.py`) that flips the registry's `protocol` field between
runs and times the actual `/v1/ws/infer/{model_name}` path — the only
endpoint that dispatches on `protocol`.

| Model   | Protocol | p50 (ms) | p95 (ms) | mean (ms) |
| ------- | -------- | -------- | -------- | --------- |
| doc-ocr | HTTP     | 394.2    | 1787.9\* | 480.1     |
| doc-ocr | gRPC     | 369.9    | 558.8    | 382.5     |
| asr     | HTTP     | 760.4    | 1148.2   | 786.0     |
| asr     | gRPC     | 739.5    | 995.5    | 740.6     |

\*First HTTP request included a cold-start outlier (1787.9ms); likely
connection/client init overhead, not representative of steady state.

### Findings

- gRPC p50 was ~6% faster for doc-ocr, ~3% faster for asr — a real but modest
  improvement, not a dramatic one.
- gRPC's tail latency (p95) was noticeably tighter for both models — no large
  outliers, unlike HTTP's cold-start spike.
- The dominant cost in both cases is model inference itself (EasyOCR ~300-400ms,
  Whisper-tiny ~600-800ms on CPU), not transport. This matches the expectation
  from Phase 3 planning: for small payloads, HTTP/JSON serialization overhead
  is negligible next to actual compute time.
- **Honest takeaway**: gRPC's main value here wasn't raw speed — it was
  type-safety (protobuf contracts) and infrastructure for future streaming
  use cases. If inference were faster (e.g. GPU-backed or a lighter model),
  the relative transport overhead of HTTP/JSON would matter more, and gRPC's
  gain would likely be larger.

### Bug found and fixed during this measurement

Initial HTTP-backed runs failed with `400 Bad Request` from the doc-ocr
worker. Root cause: the gateway's `route_request()` sent multipart file
uploads without an explicit content-type when called from the WebSocket
path, so httpx couldn't infer one from a generic filename — and the worker's
`/infer` endpoint rejects any upload that isn't `image/*`. Fixed by
explicitly setting `content_type` (and a real filename/extension) based on
`model_name` before constructing the multipart payload. This bug only
affected the WebSocket → HTTP path; the standard `/v1/infer/doc-ocr` REST
endpoint was unaffected since FastAPI's `U
ploadFile` preserves the original
client-supplied content-type.
