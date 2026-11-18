# Phase 2 — Async Job Queue

## Implementation Status: COMPLETE ✓

**Goal:** Decouple gateway from workers using job queues. Gateway returns job_id immediately, workers process asynchronously.

**Actual time:** Completed

---

## What Was Implemented

### Core Features (All Complete)

- Redis Streams job queue with consumer groups
- Dormant Kafka alternative code (not wired into the application or Compose)
- Async `/v1/infer/{model_name}` endpoint (returns job_id)
- `/v1/jobs/{job_id}` polling endpoint
- Workers filter by `worker_name` from registry
- Retry logic: Max 3 retries, exponential backoff (2s, 4s, 8s)
- Dead-letter queue (`inference_jobs_dlq`)
- Consumer lag monitoring (XLEN, XPENDING)
- Job TTLs (1h status, 2h results)
- Horizontal worker scaling

### Architecture

```
Client → Gateway → Redis Streams → Workers (async)
         ↓                                    ↓
         Returns job_id                       Store result in Redis

Client polls /v1/jobs/{job_id} for result
```

---

## Definition of Done

- [x] Gateway publishes jobs to Redis Streams
- [x] Job payload includes model_id, model_name, model_version, worker_name
- [x] Workers filter by worker_name (not hardcoded strings)
- [x] `/v1/jobs/{job_id}` returns real status
- [x] Retry logic with bounded retries + DLQ
- [x] Consumer lag monitoring logged
- [x] Stale model behavior documented
- [x] Kafka alternative code exists but is dormant and not wired/deployed
- [x] Redis Streams is the queue used by the application; Kafka code is dormant

---

## Key Design Decisions

**Why async queue?**

- Gateway never blocks on slow inference
- Workers scale independently
- Automatic retries on failures
- Better fault tolerance

**Trade-off**: Clients must poll for results (fixed in Phase 3 with WebSockets)

**Stale model behavior**: Jobs enqueued before model disabled will still process. Disabling stops NEW jobs, not in-flight ones.

**Queue selection**: Redis Streams is wired into the application. Kafka-related code is present but is not wired or deployed.

---

## Files Implemented

- `gateway/job_queue.py` - Redis Streams queue
- `gateway/kafka_queue.py` - Dormant Kafka alternative code
- `workers/doc_ocr/consumer.py` - Redis consumer with retry logic
- `workers/asr/consumer.py` - Redis consumer
- `workers/doc_ocr/kafka_consumer.py` - Dormant Kafka consumer
- `workers/asr/kafka_consumer.py` - Dormant Kafka consumer

---

## Next Phase

Phase 3: WebSockets + gRPC (eliminate polling, high-performance internal comm)
