import asyncio
import json
import logging
import os
from aiokafka import AIOKafkaConsumer
from redis.asyncio import Redis
from inference import extract_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "doc-ocr-worker"
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")


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


async def handle_job(redis: Redis, job_data: dict) -> bool:
    """
    Process a single job.

    Returns:
        bool: True if successful, False if failed
    """
    job_id = job_data["job_id"]
    payload_hex = job_data["payload"]

    try:
        logger.info(f"Processing job {job_id}")
        await update_job_status(redis, job_id, "processing")

        # Decode hex payload back to bytes
        payload = bytes.fromhex(payload_hex)

        # Run inference
        result = extract_text(payload)

        # Store result
        await update_job_status(redis, job_id, "success", result=result)
        logger.info(f"Job {job_id} completed successfully")
        return True

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        await update_job_status(redis, job_id, "failed", error=str(e))
        return False


async def consume_loop():
    """Main consumer loop for Kafka."""
    redis = Redis.from_url(REDIS_URL, decode_responses=False)

    consumer = AIOKafkaConsumer(
        "inference-jobs",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=f"{WORKER_NAME}-workers",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True
    )

    try:
        await consumer.start()
        logger.info(f"Started Kafka consumer for {WORKER_NAME}")

        async for msg in consumer:
            try:
                job_data = msg.value

                # Filter by worker_name
                if job_data.get("worker_name") != WORKER_NAME:
                    logger.debug(f"Skipping job for different worker: {job_data.get('worker_name')}")
                    continue

                # Process job
                await handle_job(redis, job_data)

            except Exception as e:
                logger.error(f"Error processing message: {e}")

    except asyncio.CancelledError:
        logger.info("Kafka consumer cancelled")
    finally:
        await consumer.stop()
        await redis.close()


if __name__ == "__main__":
    asyncio.run(consume_loop())
