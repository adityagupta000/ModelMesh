import json
import os
import uuid
from typing import Optional
from redis.asyncio import Redis

from registry import ModelRecord

PAYLOAD_TTL = int(os.getenv("PAYLOAD_TTL_SECONDS", "3600"))
STREAM_MAXLEN = int(os.getenv("STREAM_MAXLEN", "10000"))


async def enqueue_job(redis: Redis, model: ModelRecord, payload: bytes, api_key_id: str) -> str:
    """
    Enqueue an inference job to Redis Streams.

    Args:
        redis: Async Redis client
        model: ModelRecord resolved from registry
        payload: Raw file bytes to process
        api_key_id: ID of the API key making the request

    Returns:
        job_id: Unique job identifier for status tracking
    """
    job_id = str(uuid.uuid4())

    await redis.setex(f"payload:{job_id}", PAYLOAD_TTL, payload)

    await redis.xadd("inference_jobs", {
        "job_id": job_id,
        "model_id": model.id,
        "model_name": model.name,
        "model_version": model.version,
        "worker_name": model.worker_name,
        "api_key_id": api_key_id,
        "status": "queued"
    }, maxlen=STREAM_MAXLEN, approximate=True)

    # Store initial job status
    await redis.setex(
        f"job:{job_id}:status",
        3600,  # 1 hour TTL for job status
        json.dumps({
            "job_id": job_id,
            "status": "queued",
            "model_name": model.name,
            "model_version": model.version
        })
    )

    return job_id


async def get_job_status(redis: Redis, job_id: str) -> Optional[dict]:
    """
    Retrieve job status and result from Redis.

    Args:
        redis: Async Redis client
        job_id: Job identifier

    Returns:
        dict with job_id, status, and result (if available), or None if expired
    """
    # Check for result first
    result = await redis.get(f"result:{job_id}")
    if result:
        return json.loads(result)

    # Check for status
    status = await redis.get(f"job:{job_id}:status")
    if status:
        return json.loads(status)

    return None
