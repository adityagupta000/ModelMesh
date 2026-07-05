import json
import uuid
from typing import Optional
from aiokafka import AIOKafkaProducer
from redis.asyncio import Redis

from registry import ModelRecord


# Global Kafka producer (initialized at startup)
kafka_producer: Optional[AIOKafkaProducer] = None


async def init_kafka_producer(bootstrap_servers: str = "kafka:9092"):
    """Initialize Kafka producer at startup."""
    global kafka_producer
    kafka_producer = AIOKafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
    )
    await kafka_producer.start()


async def close_kafka_producer():
    """Close Kafka producer at shutdown."""
    global kafka_producer
    if kafka_producer:
        await kafka_producer.stop()


async def enqueue_job_kafka(redis: Redis, model: ModelRecord, payload: bytes, api_key_id: str) -> str:
    """
    Enqueue an inference job to Kafka.

    Args:
        redis: Async Redis client (for storing job status)
        model: ModelRecord resolved from registry
        payload: Raw file bytes to process
        api_key_id: ID of the API key making the request

    Returns:
        job_id: Unique job identifier for status tracking
    """
    if not kafka_producer:
        raise RuntimeError("Kafka producer not initialized")

    job_id = str(uuid.uuid4())

    job_data = {
        "job_id": job_id,
        "model_id": model.id,
        "model_name": model.name,
        "model_version": model.version,
        "worker_name": model.worker_name,
        "payload": payload.hex(),  # Encode bytes as hex string for JSON
        "api_key_id": api_key_id,
        "status": "queued"
    }

    # Send to Kafka, partitioned by worker_name
    await kafka_producer.send_and_wait(
        "inference-jobs",
        value=job_data,
        key=model.worker_name
    )

    # Store initial job status in Redis
    await redis.setex(
        f"job:{job_id}:status",
        3600,  # 1 hour TTL
        json.dumps({
            "job_id": job_id,
            "status": "queued",
            "model_name": model.name,
            "model_version": model.version
        })
    )

    return job_id


async def update_job_status_kafka(redis: Redis, job_id: str, status: str, result: Optional[dict] = None, error: Optional[str] = None):
    """
    Update job status in Redis (same as Redis Streams version).

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
        await redis.setex(f"result:{job_id}", 7200, json.dumps(data))
        await redis.delete(f"job:{job_id}:status")
    else:
        await redis.setex(f"job:{job_id}:status", 3600, json.dumps(data))
