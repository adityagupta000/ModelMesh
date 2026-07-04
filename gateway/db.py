import os
from databases import Database
import redis.asyncio as aioredis

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/modelmesh")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

database = Database(DATABASE_URL)
redis = aioredis.from_url(REDIS_URL, decode_responses=True)


async def connect():
    await database.connect()


async def disconnect():
    await database.disconnect()
