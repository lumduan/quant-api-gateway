"""Cache invalidation callbacks triggered after ingestion and snapshot writes.

``invalidate_overall_cache`` and ``invalidate_strategy_cache`` are
best-effort: they catch and log every exception — failures never propagate
to the caller.  ``flush_all`` is used by the admin endpoint and *does*
propagate so the operator sees whether the flush completed.
"""

import logging

from src.services.cache import invalidate_key, invalidate_pattern

logger = logging.getLogger(__name__)

OVERALL_PERFORMANCE_KEY = "overall_performance"
STRATEGY_PERFORMANCE_PREFIX = "strategy:"
STRATEGY_PERFORMANCE_SUFFIX = ":performance"
GATEWAY_CACHE_PATTERN = "gateway:*"


async def invalidate_overall_cache() -> None:
    """Delete the ``overall_performance`` cache key (best-effort).

    Called after a successful portfolio snapshot upsert. Failures are
    logged but never re-raised — the cache is a performance optimisation
    and the next read will recompute correctly on miss.
    """
    try:
        await invalidate_key(OVERALL_PERFORMANCE_KEY)
    except Exception:
        logger.exception("failed to invalidate %s", OVERALL_PERFORMANCE_KEY)


async def invalidate_strategy_cache(strategy_id: str) -> None:
    """Delete the ``strategy:{id}:performance`` cache key (best-effort).

    Called after a successful snapshot write for every active strategy
    that participated in the round. Failures are logged but never
    re-raised.
    """
    key = f"{STRATEGY_PERFORMANCE_PREFIX}{strategy_id}{STRATEGY_PERFORMANCE_SUFFIX}"
    try:
        await invalidate_key(key)
    except Exception:
        logger.exception("failed to invalidate %s", key)


async def flush_all() -> int:
    """Flush every gateway-owned cache key matching ``gateway:*``.

    Returns:
        The total count of keys deleted.

    Raises:
        CacheError: If Redis communication fails, so the admin endpoint
            can return a 500 and the operator knows the flush did not
            complete.
    """
    return await invalidate_pattern(GATEWAY_CACHE_PATTERN)
