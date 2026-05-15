"""``GET /api/v1/strategies`` — registry read endpoint."""

import logging

from fastapi import APIRouter, HTTPException, status

from src.db.postgres import get_pool
from src.schemas.registry import StrategyConfig
from src.schemas.strategy import EquityPoint
from src.services.snapshot_writer import _extract_equity_curve
from src.services.strategy_registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategies", tags=["strategies"])

_LATEST_SINGLE_STRATEGY_SQL = """
SELECT strategy_id, metadata
FROM daily_performance
WHERE strategy_id = $1
ORDER BY time DESC
LIMIT 1
"""


@router.get(
    "",
    response_model=list[StrategyConfig],
    summary="List every active strategy",
    description=(
        "Returns the active entries from ``strategies.json`` as loaded at "
        "application startup. Inactive strategies are filtered out."
    ),
)
async def list_strategies() -> list[StrategyConfig]:
    """Return the active strategies from the in-memory registry."""
    return get_registry().active_strategies()


@router.get(
    "/{strategy_id}",
    response_model=StrategyConfig,
    summary="Single strategy detail",
    description="Returns the registry entry for the given strategy.",
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def get_strategy(strategy_id: str) -> StrategyConfig:
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
    "/{strategy_id}/equity-curve",
    response_model=list[EquityPoint],
    summary="Full equity curve for a single strategy",
    description=(
        "Returns the most recent equity curve from the strategy's latest daily performance report."
    ),
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def get_strategy_equity_curve(strategy_id: str) -> list[EquityPoint]:
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
            row = await conn.fetchrow(_LATEST_SINGLE_STRATEGY_SQL, strategy_id)
    except Exception as exc:
        logger.exception("failed to query daily_performance for strategy %s", strategy_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query equity curve for strategy {strategy_id!r}",
        ) from exc

    if row is None:
        return []

    curve = _extract_equity_curve(dict(row).get("metadata"))
    return curve if curve else []
