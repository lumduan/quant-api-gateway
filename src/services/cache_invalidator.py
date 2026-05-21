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

# feature-strategies-report-metrics Phase 3 — per-strategy report cache patterns.
STRATEGY_REPORT_PATTERN = "gateway:strategy:{id}:report:*"
STRATEGY_TRADES_PATTERN = "gateway:strategy:{id}:trades:*"
STRATEGY_BENCHMARK_PATTERN = "gateway:strategy:{id}:benchmark:*"


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


async def invalidate_strategy_report_keys(strategy_id: str) -> None:
    """SCAN-delete every ``gateway:strategy:{id}:report:*`` key (best-effort).

    Called after every successful ingest so the next read recomputes
    instead of serving a stale snapshot.
    """
    pattern = STRATEGY_REPORT_PATTERN.format(id=strategy_id)
    try:
        await invalidate_pattern(pattern)
    except Exception:
        logger.exception("failed to invalidate pattern %s", pattern)


async def invalidate_strategy_trade_keys(strategy_id: str) -> None:
    """SCAN-delete every ``gateway:strategy:{id}:trades:*`` key (best-effort)."""
    pattern = STRATEGY_TRADES_PATTERN.format(id=strategy_id)
    try:
        await invalidate_pattern(pattern)
    except Exception:
        logger.exception("failed to invalidate pattern %s", pattern)


async def invalidate_strategy_benchmark_keys(strategy_id: str) -> None:
    """SCAN-delete every ``gateway:strategy:{id}:benchmark:*`` key (best-effort)."""
    pattern = STRATEGY_BENCHMARK_PATTERN.format(id=strategy_id)
    try:
        await invalidate_pattern(pattern)
    except Exception:
        logger.exception("failed to invalidate pattern %s", pattern)


async def invalidate_strategy_report_bundle(strategy_id: str) -> None:
    """Invalidate every report-related cache pattern for *strategy_id*.

    Wraps the three pattern invalidators in a single best-effort call so
    the ingest path only needs one ``await``.
    """
    await invalidate_strategy_report_keys(strategy_id)
    await invalidate_strategy_trade_keys(strategy_id)
    await invalidate_strategy_benchmark_keys(strategy_id)


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
