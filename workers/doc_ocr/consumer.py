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

WORKER_NAME = os.getenv("WORKER_NAME", "doc-ocr-worker")
CONSUMER = f"{WORKER_NAME}-{os.getenv('HOSTNAME', '1')}"
RECLAIM_IDLE_MS = int(os.getenv("RECLAIM_IDLE_MS", "300000"))
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2
STREAM_MAXLEN = int(os.getenv("STREAM_MAXLEN", "10000"))


class PermanentError(Exception):
    """Input that retrying cannot fix."""


async def update_job_status(redis: Redis, job_id: str, status: str, result: dict = None, error: str = None):
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


def decode_fields(fields):
    decoded = {}
    for key, value in fields.items():
        k = key.decode() if isinstance(key, bytes) else key
        if k == "payload":
            decoded[k] = value
        elif isinstance(value, bytes):
            decoded[k] = value.decode()
        else:
            decoded[k] = value
    return decoded


async def handle_job(redis: Redis, job_data: dict) -> tuple[Exception | None, int]:
    start = time.monotonic()
    job_id = job_data["job_id"]
    model_id = job_data.get("model_id", "")
    model_name = job_data.get("model_name", "")
    model_version = job_data.get("model_version", "")

    try:
        logger.info(f"Processing job {job_id}")
        await update_job_status(redis, job_id, "processing")
        payload = job_data.get("payload") or await redis.get(f"payload:{job_id}")
        if payload is None:
            return PermanentError("payload expired"), int((time.monotonic() - start) * 1000)

        inference_start = time.monotonic()
        result = extract_text(payload)
        elapsed_ms = int((time.monotonic() - inference_start) * 1000)
        await update_job_status(redis, job_id, "success", result=result)
        log_metric(job_id, model_id, model_name, model_version, WORKER_NAME, "success", elapsed_ms)
        logger.info(f"Job {job_id} completed successfully ({elapsed_ms}ms)")
        return None, int((time.monotonic() - start) * 1000)
    except PermanentError as exc:
        log_metric(job_id, model_id, model_name, model_version, WORKER_NAME, "error", 0)
        return exc, int((time.monotonic() - start) * 1000)
    except ValueError as exc:
        permanent = PermanentError(str(exc))
        log_metric(job_id, model_id, model_name, model_version, WORKER_NAME, "error", 0)
        return permanent, int((time.monotonic() - start) * 1000)
    except Exception as exc:
        log_metric(job_id, model_id, model_name, model_version, WORKER_NAME, "error", 0)
        return exc, int((time.monotonic() - start) * 1000)


async def fail_permanently(redis, job_data, entry_id, group, error, elapsed_ms):
    job_id = job_data["job_id"]
    logger.error(f"Job {job_id} permanently failed: {error}")
    await update_job_status(redis, job_id, "failed", error=str(error))
    await redis.xadd(
        "inference_jobs_dlq",
        {
            "job_id": job_id,
            "reason": str(error),
            "elapsed_ms": str(elapsed_ms),
        },
        maxlen=1000,
        approximate=True,
    )
    await redis.xack("inference_jobs", group, entry_id)


async def handle_failure(redis, job_data, entry_id, group, error, elapsed_ms):
    job_id = job_data["job_id"]
    retry_count = int(job_data.get("retry_count", 0))

    if retry_count < MAX_RETRIES:
        retry_count += 1
        delay = RETRY_DELAY_BASE ** retry_count
        logger.warning(f"Job {job_id} retry {retry_count}/{MAX_RETRIES} after {delay}s")
        await asyncio.sleep(delay)
        job_data["retry_count"] = str(retry_count)
        await redis.xadd(
            "inference_jobs",
            job_data,
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
        await redis.xack("inference_jobs", group, entry_id)
    else:
        logger.error(f"Job {job_id} exceeded max retries, moving to DLQ")
        await update_job_status(redis, job_id, "failed", error=str(error))
        await redis.xadd(
            "inference_jobs_dlq",
            {
                "job_id": job_id,
                "reason": str(error),
                "elapsed_ms": str(elapsed_ms),
            },
            maxlen=1000,
            approximate=True,
        )
        await redis.xack("inference_jobs", group, entry_id)


async def process_entry(redis, group, entry_id, fields):
    job_data = decode_fields(fields)
    if job_data.get("worker_name") != WORKER_NAME:
        await redis.xack("inference_jobs", group, entry_id)
        return
    error, elapsed_ms = await handle_job(redis, job_data)
    if error is None:
        await redis.xack("inference_jobs", group, entry_id)
    elif isinstance(error, PermanentError):
        await fail_permanently(redis, job_data, entry_id, group, error, elapsed_ms)
    else:
        await handle_failure(redis, job_data, entry_id, group, error, elapsed_ms)


async def log_consumer_lag(redis, group):
    try:
        stream_length = await redis.xlen("inference_jobs")
        pending_info = await redis.xpending("inference_jobs", group)
        if pending_info:
            pending_count = pending_info["pending"]
            logger.info(f"[{WORKER_NAME}] Stream length: {stream_length}, Pending: {pending_count}")
            if stream_length > STREAM_MAXLEN * 0.8:
                logger.warning(
                    f"[{WORKER_NAME}] Stream length {stream_length} exceeds 80% of limit {STREAM_MAXLEN}"
                )
    except Exception as exc:
        logger.debug(f"Could not fetch lag metrics: {exc}")


async def reclaim_stale(redis, group):
    next_id = "0-0"
    for _ in range(100):
        res = await redis.xautoclaim(
            "inference_jobs",
            group,
            CONSUMER,
            RECLAIM_IDLE_MS,
            start_id=next_id,
            count=10,
        )
        next_id = res[0]
        entries = res[1]
        if len(res) > 2 and res[2]:
            await redis.xack("inference_jobs", group, *res[2])
        for entry_id, fields in entries:
            await process_entry(redis, group, entry_id, fields)
        if next_id in (b"0-0", "0-0"):
            break


async def consume_loop():
    redis = Redis.from_url(REDIS_URL, decode_responses=False)
    group = f"{WORKER_NAME}_group"
    try:
        try:
            await redis.xgroup_create("inference_jobs", group, id="0", mkstream=True)
            logger.info(f"Created consumer group: {group}")
        except Exception:
            logger.info(f"Consumer group {group} already exists")

        logger.info(f"Starting consumer loop for {WORKER_NAME}")
        await reclaim_stale(redis, group)
        lag_counter = 0
        while True:
            try:
                messages = await redis.xreadgroup(
                    group,
                    CONSUMER,
                    {"inference_jobs": ">"},
                    count=1,
                    block=100,
                )
                lag_counter += 1
                if lag_counter >= 10:
                    await log_consumer_lag(redis, group)
                    await reclaim_stale(redis, group)
                    lag_counter = 0
                if not messages:
                    continue
                for _, entries in messages:
                    for entry_id, fields in entries:
                        await process_entry(redis, group, entry_id, fields)
            except asyncio.CancelledError:
                logger.info("Consumer loop cancelled")
                break
            except Exception as exc:
                logger.error(f"Error in consumer loop: {exc}")
                await asyncio.sleep(1)
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(consume_loop())
