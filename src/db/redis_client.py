import redis.asyncio as aioredis

from src.config import get_settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return the singleton redis.asyncio connection."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    """Close the Redis connection and null out the global reference."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
