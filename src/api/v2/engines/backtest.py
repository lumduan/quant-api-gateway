"""``GET /api/v2/engines/backtest/*`` — backtest engine endpoints.

Mirrors every v1 strategy_report endpoint. Delegates 100% to the existing
service layer — no new business logic. Same response Pydantic models as v1.
"""

import hashlib
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status

from src.config import get_settings
from src.db.csm_set_postgres import get_csm_set_pool
from src.db.postgres import get_pool
from src.schemas.strategy_report import (
    BenchmarkCurveResponse,
    BenchmarkPoint,
    StrategyReportResponse,
    TradeLogPage,
)
from src.services.cache import get_cached, set_cached
from src.services.errors import CacheError, ServiceError, StrategyReportNotFoundError
from src.services.strategy_registry import get_registry
from src.services.strategy_report_service import (
    get_benchmark_curve,
    get_latest_report,
    get_report_for_date,
    list_trades,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-backtest"])


def _ensure_active(strategy_id: str) -> None:
    """Reject requests for unknown / inactive strategies with ``404``."""
    cfg = get_registry().by_id(strategy_id)
    if cfg is None or not cfg.active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy {strategy_id!r} not found",
        )


def _params_hash(**parts: object) -> str:
    """Return a short, stable hash of cache-key parameters."""
    joined = "|".join(f"{k}={parts[k]!r}" for k in sorted(parts))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]  # noqa: S324


@router.get(
    "/strategies/{strategy_id}/report",
    response_model=StrategyReportResponse,
    summary="TradingView-style strategy report (v2)",
    responses={404: {"description": "Strategy or report not found"}},
)
async def get_strategy_report_v2(
    strategy_id: str,
    target_date: date | None = Query(
        default=None,
        alias="date",
        description="Optional snapshot date (YYYY-MM-DD). Default: latest.",
    ),
) -> StrategyReportResponse:
    """Return the strategy report (latest or for *date*)."""
    _ensure_active(strategy_id)
    settings = get_settings()
    cache_key = (
        f"gateway:strategy:{strategy_id}:report:"
        f"{target_date.isoformat() if target_date else 'latest'}"
    )

    cached = await get_cached(cache_key, StrategyReportResponse)
    if cached is not None:
        return cached

    pool = await get_pool()
    try:
        if target_date is None:
            result = await get_latest_report(pool, strategy_id=strategy_id)
        else:
            result = await get_report_for_date(
                pool, strategy_id=strategy_id, target_date=target_date
            )
    except StrategyReportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ServiceError as exc:
        logger.exception("strategy report read failed strategy_id=%s", strategy_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to load report for {strategy_id!r}",
        ) from exc

    try:
        await set_cached(cache_key, result, settings.strategy_report_ttl_seconds)
    except CacheError:
        logger.warning("cache set failed for %s; serving uncached result", cache_key)

    return result


@router.get(
    "/strategies/{strategy_id}/trades",
    response_model=TradeLogPage,
    summary="Paginated trade log for a single strategy (v2)",
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def list_strategy_trades_v2(
    strategy_id: str,
    from_date: date | None = Query(default=None, description="Lower bound (inclusive)."),
    to_date: date | None = Query(default=None, description="Upper bound (inclusive)."),
    limit: int = Query(default=100, ge=1, le=1000, description="Page size."),
    offset: int = Query(default=0, ge=0, description="Page offset."),
) -> TradeLogPage:
    """Return a page of trades for *strategy_id*."""
    _ensure_active(strategy_id)
    settings = get_settings()
    key_hash = _params_hash(from_date=from_date, to_date=to_date, limit=limit, offset=offset)
    cache_key = f"gateway:strategy:{strategy_id}:trades:{key_hash}"

    cached = await get_cached(cache_key, TradeLogPage)
    if cached is not None:
        return cached

    pool = await get_csm_set_pool()
    try:
        result = await list_trades(
            pool,
            strategy_id=strategy_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )
    except ServiceError as exc:
        logger.exception("trade log read failed strategy_id=%s", strategy_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to load trade log for {strategy_id!r}",
        ) from exc

    try:
        await set_cached(cache_key, result, settings.trade_log_ttl_seconds)
    except CacheError:
        logger.warning("cache set failed for %s; serving uncached result", cache_key)

    return result


@router.get(
    "/strategies/{strategy_id}/benchmark-curve",
    response_model=list[BenchmarkPoint],
    summary="Buy-and-hold benchmark equity curve (v2)",
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def get_strategy_benchmark_curve_v2(
    strategy_id: str,
    from_date: date | None = Query(default=None, description="Lower bound (inclusive)."),
    to_date: date | None = Query(default=None, description="Upper bound (inclusive)."),
    normalize: bool = Query(
        default=False,
        description="Scale equity values to base 100 relative to the first sample.",
    ),
) -> list[BenchmarkPoint]:
    """Return the benchmark equity curve for *strategy_id*."""
    _ensure_active(strategy_id)
    settings = get_settings()
    key_hash = _params_hash(from_date=from_date, to_date=to_date, normalize=normalize)
    cache_key = f"gateway:strategy:{strategy_id}:benchmark:{key_hash}"

    cached = await get_cached(cache_key, BenchmarkCurveResponse)
    if cached is not None:
        return list(cached.items)

    pool = await get_csm_set_pool()
    try:
        items = await get_benchmark_curve(
            pool,
            strategy_id=strategy_id,
            from_date=from_date,
            to_date=to_date,
            normalize=normalize,
        )
    except ServiceError as exc:
        logger.exception("benchmark curve read failed strategy_id=%s", strategy_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to load benchmark curve for {strategy_id!r}",
        ) from exc

    try:
        await set_cached(
            cache_key,
            BenchmarkCurveResponse(items=items),
            settings.benchmark_curve_ttl_seconds,
        )
    except CacheError:
        logger.warning("cache set failed for %s; serving uncached result", cache_key)

    return items
