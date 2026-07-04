from fastapi import HTTPException
import redis.asyncio as aioredis


async def check_rate_limit(key_id: str, limit_per_min: int, redis: aioredis.Redis) -> None:
    redis_key = f"rl:{key_id}"
    count = await redis.incr(redis_key)
    if count == 1:
        await redis.expire(redis_key, 60)
    if count > limit_per_min:
        raise HTTPException(429, "Rate limit exceeded")
