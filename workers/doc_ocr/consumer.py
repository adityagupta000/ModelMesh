import asyncio
import json
import logging
import os
import time
from redis.asyncio import Redis
from inference import extract_text
from metrics import log_metric

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "doc-ocr-worker"
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2  # seconds


async def update_job_status(redis: Redis, job_id: str, status: str, result: dict = None, error: str = None):
    """Update job status in Redis."""
    data = {"job_id": job_id, "status": status}

    if result:
        data["result"] = result

    if error:
        data["error"] = error

    if status in ("success", "failed"):
        await redis.setex(f"result:{job_id}", 7200, json.dumps(data))
        await redis.delete(f"job:{job_id}:status")
    else:
        await redis.setex(f"job:{job_id}:status", 3600, json.dumps(data))


async def handle_job(redis: Redis, job_data: dict, entry_id: str) -> bool:
    job_id = job_data["job_id"]
    payload = job_data["payload"]
    model_id = job_data.get("model_id", "")
    model_name = job_data.get("model_name", "")
    model_version = job_data.get("model_version", "")

    try:
        logger.info(f"Processing job {job_id}")
        await update_job_status(redis, job_id, "processing")

        start = time.perf_counter()
        result = extract_text(payload)
        latency_ms = int((time.perf_counter() - start) * 1000)

        await update_job_status(redis, job_id, "success", result=result)
        log_metric(job_id, model_id, model_name, model_version, WORKER_NAME, "success", latency_ms)
        logger.info(f"Job {job_id} completed successfully ({latency_ms}ms)")
        return True

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        log_metric(job_id, model_id, model_name, model_version, WORKER_NAME, "error", 0)
        return False


async def handle_failure(redis: Redis, job_data: dict, entry_id: str, group: str, error: Exception):
    """Handle job failure with retry logic and dead-letter queue."""
    job_id = job_data["job_id"]
    retry_count = int(job_data.get("retry_count", 0))

    if retry_count < MAX_RETRIES:
        # Retry with exponential backoff
        retry_count += 1
        delay = RETRY_DELAY_BASE ** retry_count
        logger.warning(f"Job {job_id} retry {retry_count}/{MAX_RETRIES} after {delay}s")

        await asyncio.sleep(delay)

        # Re-enqueue with updated retry count
        job_data["retry_count"] = str(retry_count)
        await redis.xadd("inference_jobs", job_data)

        # Acknowledge original message
        await redis.xack("inference_jobs", group, entry_id)
    else:
        # Move to dead-letter queue
        logger.error(f"Job {job_id} exceeded max retries, moving to DLQ")
        job_data["dlq_reason"] = str(error)
        job_data["dlq_timestamp"] = str(int(time.time()))
        await redis.xadd("inference_jobs_dlq", job_data)

        # Mark as failed
        await update_job_status(redis, job_id, "failed", error=str(error))

        # Acknowledge original message
        await redis.xack("inference_jobs", group, entry_id)


async def log_consumer_lag(redis: Redis, group: str):
    """Log consumer lag metrics for monitoring."""
    try:
        stream_length = await redis.xlen("inference_jobs")
        pending_info = await redis.xpending("inference_jobs", group)

        if pending_info:
            pending_count = pending_info["pending"]
            logger.info(f"[{WORKER_NAME}] Stream length: {stream_length}, Pending: {pending_count}")
    except Exception as e:
        logger.debug(f"Could not fetch lag metrics: {e}")


async def consume_loop():
    """Main consumer loop for Redis Streams."""
    redis = Redis.from_url(REDIS_URL, decode_responses=False)
    group = f"{WORKER_NAME}_group"

    try:
        # Create consumer group if it doesn't exist
        try:
            await redis.xgroup_create("inference_jobs", group, id="0", mkstream=True)
            logger.info(f"Created consumer group: {group}")
        except Exception:
            logger.info(f"Consumer group {group} already exists")

        logger.info(f"Starting consumer loop for {WORKER_NAME}")
        lag_counter = 0

        while True:
            try:
                # Read messages from the stream
                messages = await redis.xreadgroup(
                    group,
                    f"{WORKER_NAME}-1",
                    {"inference_jobs": ">"},
                    count=1,
                    block=5000
                )

                # Log consumer lag periodically (every 10 iterations)
                lag_counter += 1
                if lag_counter >= 10:
                    await log_consumer_lag(redis, group)
                    lag_counter = 0

                if not messages:
                    continue

                for stream, entries in messages:
                    for entry_id, fields in entries:
                        # Decode fields
                        job_data = {
                            k.decode() if isinstance(k, bytes) else k:
                            v.decode() if isinstance(v, bytes) and k.decode() != "payload" else v
                            for k, v in fields.items()
                        }

                        # Note: we filter by worker_name, not model status. A job enqueued before
                # the model was disabled will still process to completion. Disabling a model
                # stops new jobs from being accepted, not in-flight ones.
                # Filter by worker_name
                        if job_data.get("worker_name") != WORKER_NAME:
                            logger.debug(f"Skipping job for different worker: {job_data.get('worker_name')}")
                            await redis.xack("inference_jobs", group, entry_id)
                            continue

                        # Process job
                        success = await handle_job(redis, job_data, entry_id)

                        if success:
                            # Acknowledge successful processing
                            await redis.xack("inference_jobs", group, entry_id)
                        else:
                            # Handle failure with retry logic
                            await handle_failure(redis, job_data, entry_id, group, Exception("Processing failed"))

            except asyncio.CancelledError:
                logger.info("Consumer loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in consumer loop: {e}")
                await asyncio.sleep(1)

    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(consume_loop())
