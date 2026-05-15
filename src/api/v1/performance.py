"""``GET /api/v1/overall-performance`` and strategy performance endpoint."""

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status

from src.config import get_settings
from src.db.postgres import get_pool
from src.schemas.gateway import OverallPerformanceResponse, StrategyPerformanceResponse
from src.services.cache import get_cached, set_cached
from src.services.errors import CacheError, ServiceError
from src.services.performance import (
    compute_overall_performance,
    compute_strategy_performance,
    compute_strategy_performance_range,
)
from src.services.strategy_registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["performance"])


@router.get(
    "/overall-performance",
    response_model=OverallPerformanceResponse,
    summary="Aggregated portfolio performance",
    description=(
        "Returns the capital-weighted daily return, combined max drawdown, "
        "total portfolio value, and per-strategy allocation across every "
        "active strategy. Cached for configurable TTL."
    ),
)
async def get_overall_performance() -> OverallPerformanceResponse:
    """Compute and return the aggregated performance across all active strategies."""
    settings = get_settings()
    registry = get_registry()

    cached = await get_cached("overall_performance", OverallPerformanceResponse)
    if cached is not None:
        return cached

    pool = await get_pool()
    try:
        result = await compute_overall_performance(pool, registry)
    except ServiceError as exc:
        logger.exception("failed to compute overall performance")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute overall performance",
        ) from exc

    try:
        await set_cached("overall_performance", result, settings.overall_performance_ttl_seconds)
    except CacheError:
        logger.warning("cache set failed for overall_performance; serving uncached result")

    return result


@router.get(
    "/strategies/{strategy_id}/performance",
    response_model=StrategyPerformanceResponse | list[StrategyPerformanceResponse],
    summary="Latest or date-range performance for a single strategy",
    description=(
        "Returns the most recent daily performance snapshot for the given "
        "strategy (cached). When both ``from_date`` and ``to_date`` are "
        "provided, returns all daily snapshots in that range (uncached)."
    ),
    responses={
        404: {"description": "Strategy not found or inactive"},
        422: {"description": "Only one of from_date/to_date provided"},
    },
)
async def get_strategy_performance(
    strategy_id: str,
    from_date: date | None = Query(
        default=None, description="Start date for range query (YYYY-MM-DD)."
    ),
    to_date: date | None = Query(
        default=None, description="End date for range query (YYYY-MM-DD)."
    ),
) -> StrategyPerformanceResponse | list[StrategyPerformanceResponse]:
    """Return performance for *strategy_id* — either latest or date range."""
    settings = get_settings()
    registry = get_registry()

    cfg = registry.by_id(strategy_id)
    if cfg is None or not cfg.active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy {strategy_id!r} not found",
        )

    # Validate: both from_date and to_date must be provided together
    if (from_date is None) != (to_date is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Both from_date and to_date are required for range queries",
        )

    pool = await get_pool()

    # Range query — no caching
    if from_date is not None and to_date is not None:
        try:
            return await compute_strategy_performance_range(pool, strategy_id, from_date, to_date)
        except ServiceError as exc:
            logger.exception("failed to compute performance range for strategy %s", strategy_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to compute performance range for strategy {strategy_id!r}",
            ) from exc

    # Latest snapshot — cached
    cache_key = f"strategy:{strategy_id}:performance"
    cached = await get_cached(cache_key, StrategyPerformanceResponse)
    if cached is not None:
        return cached

    try:
        result = await compute_strategy_performance(pool, strategy_id)
    except ServiceError as exc:
        logger.exception("failed to compute performance for strategy %s", strategy_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compute performance for strategy {strategy_id!r}",
        ) from exc

    try:
        await set_cached(cache_key, result, settings.strategy_performance_ttl_seconds)
    except CacheError:
        logger.warning("cache set failed for %s; serving uncached result", cache_key)

    return result
