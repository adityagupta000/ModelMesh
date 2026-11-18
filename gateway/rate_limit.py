from fastapi import HTTPException
import redis.asyncio as aioredis


async def check_rate_limit(key_id: str, limit_per_min: int, redis: aioredis.Redis) -> None:
    redis_key = f"rl:{key_id}"
    pipe = redis.pipeline()
    pipe.set(redis_key, 0, ex=60, nx=True)
    pipe.incr(redis_key)
    _, count = await pipe.execute()
    if count > limit_per_min:
        raise HTTPException(429, "Rate limit exceeded")
