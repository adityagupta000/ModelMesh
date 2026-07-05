import json
import uuid
from typing import Optional
from redis.asyncio import Redis

from registry import ModelRecord


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

    await redis.xadd("inference_jobs", {
        "job_id": job_id,
        "model_id": model.id,
        "model_name": model.name,
        "model_version": model.version,
        "worker_name": model.worker_name,
        "payload": payload,
        "api_key_id": api_key_id,
        "status": "queued"
    })

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


async def update_job_status(redis: Redis, job_id: str, status: str, result: Optional[dict] = None, error: Optional[str] = None):
    """
    Update job status in Redis.

    Args:
        redis: Async Redis client
        job_id: Job identifier
        status: New status (processing, success, failed)
        result: Optional result data for successful jobs
        error: Optional error message for failed jobs
    """
    data = {
        "job_id": job_id,
        "status": status
    }

    if result:
        data["result"] = result

    if error:
        data["error"] = error

    if status in ("success", "failed"):
        # Store final result with longer TTL
        await redis.setex(f"result:{job_id}", 7200, json.dumps(data))
        # Clean up status key
        await redis.delete(f"job:{job_id}:status")
    else:
        # Update intermediate status
        await redis.setex(f"job:{job_id}:status", 3600, json.dumps(data))
