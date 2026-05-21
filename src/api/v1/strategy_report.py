"""Strategy-report read endpoints (feature-strategies-report-metrics Phase 3).

Three open (no ``X-API-Key``) GET endpoints under
``/api/v1/strategies/{strategy_id}/`` that the dashboard renders into the
TradingView-style report:

* ``/report`` — JSONB snapshot (latest or for a specific date).
* ``/trades`` — paginated trade log from ``db_csm_set.trade_history``.
* ``/benchmark-curve`` — buy-and-hold equity curve from
  ``db_csm_set.benchmark_equity_curve``.

All three reads use cache-aside via Redis with the TTLs configured by
:class:`~src.config.Settings`. Cache failures degrade gracefully — every
handler logs a warning and serves the freshly-computed value.
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

router = APIRouter(prefix="/strategies", tags=["strategy-report"])


def _ensure_active(strategy_id: str) -> None:
    """Reject requests for unknown / inactive strategies with ``404``.

    Args:
        strategy_id: The path-parameter strategy identifier.

    Raises:
        HTTPException: ``404`` if the registry has no active entry.
    """
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
    "/{strategy_id}/report",
    response_model=StrategyReportResponse,
    summary="TradingView-style strategy report (latest or for a date)",
    description=(
        "Returns the most recent ``strategy_report_snapshot`` row for the "
        "given strategy, or — when ``date`` is provided — the snapshot for "
        "that calendar day. The response is cached for "
        "``STRATEGY_REPORT_TTL_SECONDS``."
    ),
    responses={404: {"description": "Strategy or report not found"}},
)
async def get_strategy_report(
    strategy_id: str,
    target_date: date | None = Query(
        default=None,
        alias="date",
        description="Optional snapshot date (YYYY-MM-DD). Default: latest.",
    ),
) -> StrategyReportResponse:
    """Return the strategy report (latest or for *date*).

    Raises:
        HTTPException: ``404`` if the strategy or snapshot is missing;
            ``500`` if the database read fails.
    """
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
    "/{strategy_id}/trades",
    response_model=TradeLogPage,
    summary="Paginated trade log for a single strategy",
    description=(
        "Returns a page of ``trade_history`` rows from ``db_csm_set``. "
        "Cached per ``(strategy_id, from_date, to_date, limit, offset)`` "
        "for ``TRADE_LOG_TTL_SECONDS``."
    ),
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def list_strategy_trades(
    strategy_id: str,
    from_date: date | None = Query(default=None, description="Lower bound (inclusive)."),
    to_date: date | None = Query(default=None, description="Upper bound (inclusive)."),
    limit: int = Query(default=100, ge=1, le=1000, description="Page size."),
    offset: int = Query(default=0, ge=0, description="Page offset."),
) -> TradeLogPage:
    """Return a page of trades for *strategy_id*.

    Raises:
        HTTPException: ``404`` if the strategy is unknown / inactive;
            ``500`` if the database read fails.
    """
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
    "/{strategy_id}/benchmark-curve",
    response_model=list[BenchmarkPoint],
    summary="Buy-and-hold benchmark equity curve",
    description=(
        "Returns the benchmark equity curve from "
        "``db_csm_set.benchmark_equity_curve``. When ``normalize=true``, "
        "every value is scaled to base 100 relative to the first sample. "
        "Cached for ``BENCHMARK_CURVE_TTL_SECONDS``."
    ),
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def get_strategy_benchmark_curve(
    strategy_id: str,
    from_date: date | None = Query(default=None, description="Lower bound (inclusive)."),
    to_date: date | None = Query(default=None, description="Upper bound (inclusive)."),
    normalize: bool = Query(
        default=False,
        description="Scale equity values to base 100 relative to the first sample.",
    ),
) -> list[BenchmarkPoint]:
    """Return the benchmark equity curve for *strategy_id*.

    Raises:
        HTTPException: ``404`` if the strategy is unknown / inactive;
            ``500`` if the database read fails.
    """
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
