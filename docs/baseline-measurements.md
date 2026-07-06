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
