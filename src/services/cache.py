"""Async Redis cache layer for the gateway.

Stores Pydantic models as JSON with configurable TTLs. Every public function
crosses module boundaries with typed models (not raw dicts), satisfying the
"Pydantic at boundaries" hard rule.
"""

import json
import logging
from typing import TypeVar

from pydantic import BaseModel

from src.db.redis_client import get_redis
from src.services.errors import CacheError

logger = logging.getLogger(__name__)

_T = TypeVar("_T", bound=BaseModel)


async def get_cached(key: str, model_type: type[_T]) -> _T | None:
    """Return a cached Pydantic model, or ``None`` on cache miss.

    Args:
        key: The Redis key to fetch.
        model_type: The Pydantic model class to deserialize into.

    Returns:
        The deserialized model instance, or ``None`` if the key does not
        exist or the stored JSON is corrupt (graceful degradation — the
        caller falls through to recompute).

    Raises:
        CacheError: If Redis communication fails (connection refused,
            timeout, etc.).
    """
    try:
        redis = await get_redis()
        raw = await redis.get(key)
    except Exception as exc:
        logger.error("redis GET failed for key %s: %s", key, exc)
        raise CacheError(f"redis GET failed for key {key!r}") from exc

    if raw is None:
        logger.debug("cache miss: %s", key)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cache key %s has corrupt JSON; treating as miss", key)
        return None

    try:
        return model_type.model_validate(data)
    except Exception as exc:
        logger.warning("cache key %s failed model validation; treating as miss: %s", key, exc)
        return None


async def set_cached(key: str, value: BaseModel, ttl: int) -> None:
    """Cache a Pydantic model with a TTL (seconds).

    Serializes via :meth:`BaseModel.model_dump_json` so ``Decimal``,
    ``datetime``, and other Pydantic-native types are handled correctly.

    Args:
        key: The Redis key to set.
        value: Any Pydantic model instance.
        ttl: Time-to-live in seconds.

    Raises:
        CacheError: If Redis communication fails.
    """
    try:
        payload = value.model_dump_json()
    except Exception as exc:
        raise CacheError(f"failed to serialise model for key {key!r}") from exc

    try:
        redis = await get_redis()
        await redis.setex(key, ttl, payload)
        logger.debug("cached key %s with TTL %d", key, ttl)
    except Exception as exc:
        logger.error("redis SETEX failed for key %s: %s", key, exc)
        raise CacheError(f"redis SETEX failed for key {key!r}") from exc


async def invalidate_key(key: str) -> None:
    """Delete a single cache key. No-op if the key does not exist.

    Raises:
        CacheError: If Redis communication fails.
    """
    try:
        redis = await get_redis()
        await redis.delete(key)
        logger.debug("invalidated cache key %s", key)
    except Exception as exc:
        logger.error("redis DELETE failed for key %s: %s", key, exc)
        raise CacheError(f"redis DELETE failed for key {key!r}") from exc


async def invalidate_pattern(pattern: str) -> int:
    """Delete every key matching a glob pattern via SCAN + DELETE.

    Uses ``SCAN`` (non-blocking, cursor-based iteration) rather than
    ``KEYS`` so that large key spaces do not stall the Redis event loop.

    Returns:
        The total count of keys deleted.

    Raises:
        CacheError: If Redis communication fails.
    """
    deleted = 0
    try:
        redis = await get_redis()
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=pattern)
            if keys:
                await redis.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
    except Exception as exc:
        logger.error("redis SCAN/DELETE failed for pattern %s: %s", pattern, exc)
        raise CacheError(f"redis SCAN/DELETE failed for pattern {pattern!r}") from exc
    logger.info("invalidated %d keys matching pattern %s", deleted, pattern)
    return deleted
