"""``GET /api/v2/engines/portfolio/*`` — portfolio engine endpoints.

Mirrors every v1 portfolio, performance, equity-curve, and strategies endpoint.
Delegates 100% to the existing service layer — no new business logic.
Same response Pydantic models as v1 (imported and reused, not duplicated).
"""

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status

from src.api.v1.strategies import _LATEST_SINGLE_STRATEGY_SQL as _SINGLE_STRATEGY_SQL
from src.config import get_settings
from src.db.postgres import get_pool
from src.schemas.gateway import (
    OverallPerformanceResponse,
    PortfolioSnapshotResponse,
    StrategyPerformanceResponse,
)
from src.schemas.registry import StrategyConfig
from src.schemas.strategy import EquityPoint
from src.services.cache import get_cached, set_cached
from src.services.errors import CacheError, ServiceError
from src.services.performance import (
    compute_overall_performance,
    compute_strategy_performance,
    compute_strategy_performance_range,
)
from src.services.portfolio import (
    compute_portfolio_equity_curve,
    query_latest_snapshot,
    query_snapshot_by_date,
)
from src.services.snapshot_writer import _extract_equity_curve, build_equity_curve_from_rows
from src.services.strategy_registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-portfolio"])


# --------------------------------------------------------------------------- #
# Portfolio snapshot endpoints                                                #
# --------------------------------------------------------------------------- #


@router.get(
    "/snapshot",
    response_model=PortfolioSnapshotResponse,
    summary="Latest portfolio snapshot (v2)",
)
async def get_latest_snapshot_v2() -> PortfolioSnapshotResponse:
    """Return the most recent portfolio snapshot."""
    settings = get_settings()
    cached = await get_cached("portfolio_snapshot:latest", PortfolioSnapshotResponse)
    if cached is not None:
        return cached

    pool = await get_pool()
    try:
        result = await query_latest_snapshot(pool)
    except ServiceError as exc:
        logger.exception("failed to query latest portfolio snapshot")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to query portfolio snapshot",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No portfolio snapshots available",
        )

    try:
        await set_cached(
            "portfolio_snapshot:latest", result, settings.portfolio_snapshot_ttl_seconds
        )
    except CacheError:
        logger.warning("cache set failed for portfolio_snapshot:latest; serving uncached result")

    return result


@router.get(
    "/snapshot/{snapshot_date}",
    response_model=PortfolioSnapshotResponse,
    summary="Portfolio snapshot for a specific date (v2)",
    responses={404: {"description": "No snapshot for that date"}},
)
async def get_snapshot_by_date_v2(snapshot_date: date) -> PortfolioSnapshotResponse:
    """Return the portfolio snapshot for *snapshot_date*."""
    settings = get_settings()
    cache_key = f"portfolio_snapshot:{snapshot_date.isoformat()}"
    cached = await get_cached(cache_key, PortfolioSnapshotResponse)
    if cached is not None:
        return cached

    pool = await get_pool()
    try:
        result = await query_snapshot_by_date(pool, snapshot_date)
    except ServiceError as exc:
        logger.exception("failed to query portfolio snapshot for %s", snapshot_date.isoformat())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query portfolio snapshot for {snapshot_date.isoformat()}",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No portfolio snapshot for {snapshot_date.isoformat()}",
        )

    try:
        await set_cached(cache_key, result, settings.portfolio_snapshot_ttl_seconds)
    except CacheError:
        logger.warning("cache set failed for %s; serving uncached result", cache_key)

    return result


# --------------------------------------------------------------------------- #
# Portfolio equity curve                                                      #
# --------------------------------------------------------------------------- #


@router.get(
    "/equity-curve",
    response_model=list[EquityPoint],
    summary="Merged portfolio equity curve (v2)",
)
async def get_portfolio_equity_curve_v2(
    normalize: bool = Query(
        default=True,
        description="Whether to normalize each input curve to base 100 before merging.",
    ),
) -> list[EquityPoint]:
    """Merge equity curves from all active strategies into a portfolio curve."""
    registry = get_registry()
    pool = await get_pool()
    try:
        result = await compute_portfolio_equity_curve(pool, registry, normalize=normalize)
    except ServiceError as exc:
        logger.exception("failed to compute portfolio equity curve")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute portfolio equity curve",
        ) from exc
    return result


# --------------------------------------------------------------------------- #
# Performance endpoints                                                       #
# --------------------------------------------------------------------------- #


@router.get(
    "/overall-performance",
    response_model=OverallPerformanceResponse,
    summary="Aggregated portfolio performance (v2)",
)
async def get_overall_performance_v2() -> OverallPerformanceResponse:
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
    summary="Latest or date-range performance for a single strategy (v2)",
    responses={
        404: {"description": "Strategy not found or inactive"},
        422: {"description": "Only one of from_date/to_date provided"},
    },
)
async def get_strategy_performance_v2(
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

    if (from_date is None) != (to_date is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Both from_date and to_date are required for range queries",
        )

    pool = await get_pool()

    if from_date is not None and to_date is not None:
        try:
            return await compute_strategy_performance_range(pool, strategy_id, from_date, to_date)
        except ServiceError as exc:
            logger.exception("failed to compute performance range for strategy %s", strategy_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to compute performance range for strategy {strategy_id!r}",
            ) from exc

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


# --------------------------------------------------------------------------- #
# Strategies endpoints                                                        #
# --------------------------------------------------------------------------- #


@router.get(
    "/strategies",
    response_model=list[StrategyConfig],
    summary="List every active strategy (v2)",
)
async def list_strategies_v2() -> list[StrategyConfig]:
    """Return the active strategies from the in-memory registry."""
    return get_registry().active_strategies()


@router.get(
    "/strategies/{strategy_id}",
    response_model=StrategyConfig,
    summary="Single strategy detail (v2)",
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def get_strategy_v2(strategy_id: str) -> StrategyConfig:
    """Return the registry entry for *strategy_id*."""
    registry = get_registry()
    cfg = registry.by_id(strategy_id)
    if cfg is None or not cfg.active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy {strategy_id!r} not found",
        )
    return cfg


@router.get(
    "/strategies/{strategy_id}/equity-curve",
    response_model=list[EquityPoint],
    summary="Full equity curve for a single strategy (v2)",
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def get_strategy_equity_curve_v2(strategy_id: str) -> list[EquityPoint]:
    """Return the latest equity curve for *strategy_id*."""
    registry = get_registry()
    cfg = registry.by_id(strategy_id)
    if cfg is None or not cfg.active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy {strategy_id!r} not found",
        )

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SINGLE_STRATEGY_SQL, strategy_id)
    except Exception as exc:
        logger.exception("failed to query daily_performance for strategy %s", strategy_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query equity curve for strategy {strategy_id!r}",
        ) from exc

    if row is None:
        return []

    curve = _extract_equity_curve(dict(row).get("metadata"))
    if curve:
        return curve

    async with pool.acquire() as conn:
        return await build_equity_curve_from_rows(conn, strategy_id)
