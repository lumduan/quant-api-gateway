"""``GET /api/v1/portfolio/*`` — portfolio snapshot and equity-curve endpoints."""

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status

from src.config import get_settings
from src.db.postgres import get_pool
from src.schemas.gateway import PortfolioSnapshotResponse
from src.schemas.strategy import EquityPoint
from src.services.cache import get_cached, set_cached
from src.services.errors import CacheError, ServiceError
from src.services.portfolio import (
    compute_portfolio_equity_curve,
    query_latest_snapshot,
    query_snapshot_by_date,
)
from src.services.strategy_registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get(
    "/snapshot",
    response_model=PortfolioSnapshotResponse,
    summary="Latest portfolio snapshot",
    description=(
        "Returns the most recent daily portfolio snapshot row. Cached for configurable TTL."
    ),
)
async def get_latest_snapshot() -> PortfolioSnapshotResponse:
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
    summary="Portfolio snapshot for a specific date",
    description=(
        "Returns the portfolio snapshot for the given date (YYYY-MM-DD). "
        "Cached for configurable TTL."
    ),
    responses={404: {"description": "No snapshot for that date"}},
)
async def get_snapshot_by_date(snapshot_date: date) -> PortfolioSnapshotResponse:
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


@router.get(
    "/equity-curve",
    response_model=list[EquityPoint],
    summary="Merged portfolio equity curve",
    description=(
        "Merges equity curves from every active strategy into a single "
        "weighted portfolio curve. Set ``normalize=false`` to skip "
        "base-100 normalization."
    ),
)
async def get_portfolio_equity_curve(
    normalize: bool = Query(
        default=True,
        description="Whether to normalize each input curve to base 100 before merging.",
    ),
) -> list[EquityPoint]:
    """Merge equity curves from all active strategies into a portfolio curve."""
    registry = get_registry()
    pool = await get_pool()
    try:
        result = await compute_portfolio_equity_curve(pool, registry)
    except ServiceError as exc:
        logger.exception("failed to compute portfolio equity curve")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute portfolio equity curve",
        ) from exc
    return result
