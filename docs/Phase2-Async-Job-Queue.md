# Phase 2 — Async Job Queue

### ModelMesh Implementation Guide

**Goal:** Decouple the gateway from direct model calls. Instead of the gateway blocking on inference, it publishes a job (resolved through the Model Registry) and returns a job ID; workers consume jobs matching their registered `worker_name` independently.

**Estimated time:** 5–7 days

**Prerequisite:** Phase 1 fully complete and checked off, including the Model Registry.

---

## 1. Why this phase matters (read before building)

Right now your gateway calls a worker and waits. That's fine at low volume, but it means one slow ASR request blocks capacity for everyone else, and if a worker crashes mid-request, the client just gets an error with no retry path. A queue fixes both: the gateway's job is just "resolve the model via the registry, then hand the job off," and workers pull jobs at their own pace, can retry on failure, and can scale independently of the gateway.

This is also the part of the JD ("event-driven and streaming architectures using Kafka and Redis Streams") that most junior candidates skip entirely because it's unglamorous. Doing it properly — including handling backpressure — is a real differentiator.

---

## 2. Start with Redis Streams (do this first)

Lower learning curve than Kafka, same conceptual pattern (append-only log, consumer groups, acknowledgment). Get this working before touching Kafka.

### 2.1 Job schema — now registry-aware

The job now carries `worker_name` (resolved from the registry), not just a raw `model_type` string. This matters: it's what lets multiple versions of the same model (Phase 5's canary) route to different workers without the queue needing special-case logic.

```python
# gateway/queue.py
import json
import uuid

async def enqueue_job(redis, model, payload: bytes, api_key_id: str) -> str:
    """model is a registry.ModelRecord, resolved via registry.get_model() first."""
    job_id = str(uuid.uuid4())
    await redis.xadd("inference_jobs", {
        "job_id": job_id,
        "model_id": model.id,
        "model_name": model.name,
        "model_version": model.version,
        "worker_name": model.worker_name,     # NEW — this is what workers filter on
        "payload": payload,
        "api_key_id": api_key_id,
        "status": "queued"
    })
    return job_id
```

### 2.2 Gateway endpoint changes

The gateway resolves the model via the registry, then enqueues instead of calling the worker directly:

```python
@app.post("/v1/infer/{model_name}")
async def infer(model_name: str, file: UploadFile, key=Depends(verify_api_key)):
    await check_rate_limit(key.id, key.rate_limit_per_min, redis)

    model = await registry.get_model(db, model_name)
    if not model:
        raise HTTPException(404, "Unknown model")
    if model.status != "active":
        raise HTTPException(503, "Model is currently disabled")

    payload = await file.read()
    job_id = await enqueue_job(redis, model, payload, key.id)
    return {"job_id": job_id, "status": "queued"}

@app.get("/v1/jobs/{job_id}")
async def get_job_status(job_id: str, key=Depends(verify_api_key)):
    result = await redis.get(f"result:{job_id}")
    if result:
        return json.loads(result)
    return {"job_id": job_id, "status": "processing"}
```

This changes your API contract — clients now poll for results (or use the WebSocket you'll build in Phase 3). That's a real, honest architectural tradeoff worth being able to explain: synchronous APIs are simpler for clients but couple availability; async queues improve resilience at the cost of a more complex client experience.

### 2.3 Worker consumer — filters by `worker_name`, not a hardcoded string

```python
# workers/plant_health/consumer.py
WORKER_NAME = "plant-worker"   # must match models.worker_name in the registry

async def consume_loop(redis):
    group = f"{WORKER_NAME}_group"
    try:
        await redis.xgroup_create("inference_jobs", group, id="0", mkstream=True)
    except Exception:
        pass  # group already exists

    while True:
        messages = await redis.xreadgroup(
            group, "worker-1", {"inference_jobs": ">"}, count=1, block=5000
        )
        for stream, entries in messages:
            for entry_id, fields in entries:
                if fields[b"worker_name"].decode() != WORKER_NAME:
                    continue  # not for this worker
                try:
                    result = predict(fields[b"payload"])
                    await redis.set(f"result:{fields[b'job_id'].decode()}", json.dumps(result))
                    await redis.xack("inference_jobs", group, entry_id)
                except Exception as e:
                    await handle_failure(redis, fields, entry_id, e)
```

Because filtering happens on `worker_name` (a registry field) rather than a string baked into the consumer's `if/elif`, registering a new model that reuses an existing worker, or standing up a new worker for a brand-new model, needs zero changes to this consumer loop's structure — only a new registry row and, if needed, a new consumer process with its own `WORKER_NAME`.

---

## 3. Backpressure and failure handling (don't skip this)

This is the part that separates "used Redis Streams" from "understands why you'd use it."

- **Consumer lag:** if workers fall behind, `XLEN` on the stream grows. Log this metric now — you'll wire it into Grafana in Phase 5, broken down per `worker_name`.
- **Retry with limits:** on failure, re-add the job with a retry count; after N retries, move it to a dead-letter stream (`inference_jobs_dlq`) instead of retrying forever.
- **Idempotency:** if a worker crashes after processing but before acking, the job gets redelivered. Make sure writing the result twice doesn't cause problems.
- **Timeouts:** what happens if a client polls `/v1/jobs/{job_id}` and the job never completes? Add an expiry so stale jobs report a clear "failed/timeout" status rather than hanging forever.
- **Stale registry entries:** what happens if a job references a `model_id` that gets disabled mid-flight? Decide and document the behavior (finish in-flight jobs, or reject at consume time) — this is a legitimate edge case worth having an answer for.

Write a short note-to-self on each of these decisions as you make them — you'll want this for interview answers later.

---

## 4. Now do it again with Kafka (if time allows)

Once Redis Streams works end-to-end, redo the same flow with Kafka, keyed the same way — partition or route by `worker_name` so each worker type's consumer group only sees its own jobs.

```python
# Kafka producer (gateway side)
from aiokafka import AIOKafkaProducer

producer = AIOKafkaProducer(bootstrap_servers="localhost:9092")
await producer.send_and_wait(
    "inference-jobs",
    json.dumps(job).encode(),
    key=model.worker_name.encode()   # partition key ties jobs to worker type
)
```

```python
# Kafka consumer (worker side)
from aiokafka import AIOKafkaConsumer

consumer = AIOKafkaConsumer(
    "inference-jobs",
    bootstrap_servers="localhost:9092",
    group_id=f"{WORKER_NAME}-workers"
)
async for msg in consumer:
    job = json.loads(msg.value)
    if job["worker_name"] != WORKER_NAME:
        continue
    ...
```

Run Kafka locally via Docker Compose (`bitnami/kafka` with KRaft mode is the simplest single-container setup, no Zookeeper needed).

### The comparison you should be able to state clearly afterward:

|                     | Redis Streams                                  | Kafka                                                      |
| ------------------- | ---------------------------------------------- | ---------------------------------------------------------- |
| Ops complexity      | Low — just Redis                               | Higher — brokers, partitions, topics                       |
| Durability          | Good, tied to Redis persistence config         | Strong — designed for it, configurable retention           |
| Throughput at scale | Good for moderate load                         | Built for very high throughput, multi-consumer fan-out     |
| Replay              | Limited                                        | Native — can replay from any offset                        |
| When to pick which  | Simpler systems, lower ops overhead acceptable | High-volume, multi-team, need durability/replay guarantees |

Being able to state this tradeoff from having actually built both, rather than reciting it, is exactly the kind of answer that lands well in an interview.

---

## 5. Definition of done

- [ ] Gateway resolves the model via the registry, then publishes jobs to Redis Streams (or Kafka) — no hardcoded model names in the queue path
- [ ] Job payload carries `model_id`, `model_name`, `model_version`, and `worker_name`
- [ ] Workers filter by `worker_name`, not a hardcoded string comparison to a model name
- [ ] `/v1/jobs/{job_id}` endpoint returns real status (queued/processing/success/failed)
- [ ] Retry logic with a bounded retry count and a dead-letter path
- [ ] Consumer lag is logged/observable per `worker_name` (even just a print statement for now — Grafana comes in Phase 5)
- [ ] Documented behavior for a job whose model gets disabled mid-flight
- [ ] You've written a short explanation of Redis Streams vs. Kafka tradeoffs based on your own build, not just documentation

Once checked off, move to Phase 3.
