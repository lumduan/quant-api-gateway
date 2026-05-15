"""Business logic for computing aggregated and per-strategy performance.

Reads the latest ``daily_performance`` rows from Postgres, converts them into
response schemas, and delegates aggregation to :mod:`src.services.aggregator`.
"""

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from src.schemas.gateway import OverallPerformanceResponse, StrategyPerformanceResponse
from src.schemas.registry import StrategyRegistry
from src.schemas.strategy import EquityPoint
from src.services.aggregator import calculate_combined_drawdown, calculate_weighted_return
from src.services.errors import ServiceError
from src.services.snapshot_writer import _extract_equity_curve

logger = logging.getLogger(__name__)

_LATEST_PER_STRATEGY_SQL = """
SELECT DISTINCT ON (strategy_id)
    strategy_id, total_value, daily_return, max_drawdown, sharpe_ratio, time, metadata
FROM daily_performance
WHERE strategy_id = ANY($1::text[])
ORDER BY strategy_id, time DESC
"""

_LATEST_SINGLE_STRATEGY_SQL = """
SELECT strategy_id, total_value, daily_return, max_drawdown, sharpe_ratio, time, metadata
FROM daily_performance
WHERE strategy_id = $1
ORDER BY time DESC
LIMIT 1
"""

_RANGE_SINGLE_STRATEGY_SQL = """
SELECT strategy_id, total_value, daily_return, max_drawdown, sharpe_ratio, time, metadata
FROM daily_performance
WHERE strategy_id = $1 AND time::date BETWEEN $2 AND $3
ORDER BY time ASC
"""


def _row_to_strategy_performance(row: dict[str, Any]) -> StrategyPerformanceResponse:
    """Convert a ``daily_performance`` row into a :class:`StrategyPerformanceResponse`."""
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    daily_pnl_str = metadata.get("daily_pnl", "0")
    try:
        daily_pnl = Decimal(str(daily_pnl_str))
    except (ValueError, ArithmeticError):
        daily_pnl = Decimal("0")

    t = row["time"]
    last_updated = t if hasattr(t, "tzinfo") and t.tzinfo is not None else t.replace(tzinfo=UTC)

    return StrategyPerformanceResponse(
        strategy_id=row["strategy_id"],
        daily_pnl=daily_pnl,
        total_value=Decimal(str(row["total_value"])),
        max_drawdown=Decimal(str(row["max_drawdown"])),
        sharpe_ratio=Decimal(str(row["sharpe_ratio"])),
        last_updated=last_updated,
    )


async def compute_overall_performance(
    pool: asyncpg.Pool,
    registry: StrategyRegistry,
) -> OverallPerformanceResponse:
    """Query latest per-strategy rows, compute aggregates, build response.

    Args:
        pool: The asyncpg pool for ``db_gateway``.
        registry: The strategy registry loaded at startup.

    Returns:
        An :class:`OverallPerformanceResponse` — zero-valued fields when no
        active strategies or no rows exist.

    Raises:
        ServiceError: If Postgres rejects the query.
    """
    active = registry.active_strategies()
    weights: dict[str, float] = {}
    for cfg in active:
        weights[cfg.id] = float(cfg.capital_weight)

    strategies: list[StrategyPerformanceResponse] = []
    curves: dict[str, list[EquityPoint]] = {}

    if active:
        active_ids = [cfg.id for cfg in active]
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_LATEST_PER_STRATEGY_SQL, active_ids)
        except asyncpg.PostgresError as exc:
            raise ServiceError("failed to query daily_performance for overall aggregation") from exc

        for row in rows:
            r = dict(row)
            sp = _row_to_strategy_performance(r)
            strategies.append(sp)
            curve = _extract_equity_curve(r.get("metadata"))
            if curve:
                curves[sp.strategy_id] = curve

    total_portfolio_value = sum(s.total_value for s in strategies)
    weighted_return = calculate_weighted_return(strategies, weights) if strategies else Decimal("0")

    combined_drawdown_val = calculate_combined_drawdown(curves, weights) if curves else 0.0

    total_weight = sum(weights.values())
    if total_weight > 0:
        allocation = {sid: Decimal(str(w / total_weight)) for sid, w in weights.items()}
    else:
        allocation = {sid: Decimal("0") for sid in weights}

    tv = (
        total_portfolio_value
        if isinstance(total_portfolio_value, Decimal)
        else Decimal(str(total_portfolio_value))
    )
    wr_raw = (
        weighted_return if isinstance(weighted_return, Decimal) else Decimal(str(weighted_return))
    )
    wr = wr_raw.quantize(Decimal("0.000001"))
    dd = Decimal(str(combined_drawdown_val)).quantize(Decimal("0.0001"))
    return OverallPerformanceResponse(
        total_portfolio_value=tv,
        weighted_daily_return=wr,
        combined_max_drawdown=dd,
        active_strategies=len(active),
        allocation=allocation,
        strategies=strategies,
        computed_at=datetime.now(UTC),
    )


async def compute_strategy_performance(
    pool: asyncpg.Pool,
    strategy_id: str,
) -> StrategyPerformanceResponse:
    """Query the latest ``daily_performance`` row for a single strategy.

    Args:
        pool: The asyncpg pool for ``db_gateway``.
        strategy_id: The strategy to query.

    Returns:
        A :class:`StrategyPerformanceResponse`.

    Raises:
        ServiceError: If no row exists for the strategy or Postgres fails.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_LATEST_SINGLE_STRATEGY_SQL, strategy_id)
    except asyncpg.PostgresError as exc:
        raise ServiceError(f"failed to query daily_performance for strategy {strategy_id}") from exc

    if row is None:
        raise ServiceError(f"no performance data for strategy {strategy_id}")

    return _row_to_strategy_performance(dict(row))


async def compute_strategy_performance_range(
    pool: asyncpg.Pool,
    strategy_id: str,
    from_date: date,
    to_date: date,
) -> list[StrategyPerformanceResponse]:
    """Query ``daily_performance`` rows for *strategy_id* in a date range.

    Args:
        pool: The asyncpg pool for ``db_gateway``.
        strategy_id: The strategy to query.
        from_date: Inclusive start date.
        to_date: Inclusive end date.

    Returns:
        A list of :class:`StrategyPerformanceResponse` ordered by ``time ASC``.
        Returns an empty list when no rows fall in the range (not an error).

    Raises:
        ServiceError: If Postgres rejects the query.
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_RANGE_SINGLE_STRATEGY_SQL, strategy_id, from_date, to_date)
    except asyncpg.PostgresError as exc:
        raise ServiceError(
            f"failed to query daily_performance for strategy {strategy_id} in range"
        ) from exc

    return [_row_to_strategy_performance(dict(row)) for row in rows]
