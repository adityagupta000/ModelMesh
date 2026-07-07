# Phase 2 — Async Job Queue

## Implementation Status: COMPLETE ✓

**Goal:** Decouple gateway from workers using job queues. Gateway returns job_id immediately, workers process asynchronously.

**Actual time:** Completed

---

## What Was Implemented

### Core Features (All Complete)
- Redis Streams job queue with consumer groups
- Kafka alternative (KRaft mode, no Zookeeper)
- Async `/v1/infer/{model_name}` endpoint (returns job_id)
- `/v1/jobs/{job_id}` polling endpoint
- Workers filter by `worker_name` from registry
- Retry logic: Max 3 retries, exponential backoff (2s, 4s, 8s)
- Dead-letter queue (`inference_jobs_dlq`)
- Consumer lag monitoring (XLEN, XPENDING)
- Idempotent job processing
- Job TTLs (1h status, 2h results)
- Horizontal worker scaling

### Architecture
```
Client → Gateway → Redis Streams / Kafka → Workers (async)
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
- [x] Kafka alternative implementation
- [x] Redis Streams vs Kafka comparison

---

## Key Design Decisions

**Why async queue?**
- Gateway never blocks on slow inference
- Workers scale independently
- Automatic retries on failures
- Better fault tolerance

**Trade-off**: Clients must poll for results (fixed in Phase 3 with WebSockets)

**Stale model behavior**: Jobs enqueued before model disabled will still process. Disabling stops NEW jobs, not in-flight ones.

**Redis Streams vs Kafka**: Default to Redis Streams (simpler ops). Kafka available for high-volume or multi-consumer scenarios.

---

## Files Implemented

- `gateway/job_queue.py` - Redis Streams queue (renamed from `queue.py` to avoid Python builtin conflict)
- `gateway/kafka_queue.py` - Kafka alternative
- `workers/doc_ocr/consumer.py` - Redis consumer with retry logic
- `workers/asr/consumer.py` - Redis consumer
- `workers/doc_ocr/kafka_consumer.py` - Kafka consumer
- `workers/asr/kafka_consumer.py` - Kafka consumer

---

## Next Phase

Phase 3: WebSockets + gRPC (eliminate polling, high-performance internal comm)
