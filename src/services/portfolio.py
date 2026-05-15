"""Business logic for querying portfolio snapshots and computing merged equity curves."""

import json
import logging
from datetime import UTC, date
from decimal import Decimal
from typing import Any

import asyncpg

from src.schemas.gateway import PortfolioSnapshotResponse
from src.schemas.registry import StrategyRegistry
from src.schemas.strategy import EquityPoint
from src.services.aggregator import merge_equity_curves
from src.services.errors import ServiceError
from src.services.snapshot_writer import _extract_equity_curve

logger = logging.getLogger(__name__)

_LATEST_SNAPSHOT_SQL = """
SELECT time, total_portfolio, weighted_return, combined_drawdown,
       active_strategies, allocation
FROM portfolio_snapshot
ORDER BY time DESC
LIMIT 1
"""

_SNAPSHOT_BY_DATE_SQL = """
SELECT time, total_portfolio, weighted_return, combined_drawdown,
       active_strategies, allocation
FROM portfolio_snapshot
WHERE time::date = $1
"""

_LATEST_PER_STRATEGY_FOR_EQUITY_SQL = """
SELECT DISTINCT ON (strategy_id)
    strategy_id, metadata
FROM daily_performance
WHERE strategy_id = ANY($1::text[])
ORDER BY strategy_id, time DESC
"""


def _row_to_snapshot_response(row: dict[str, Any]) -> PortfolioSnapshotResponse:
    """Convert a ``portfolio_snapshot`` row into a :class:`PortfolioSnapshotResponse`."""
    allocation_raw = row.get("allocation")
    if isinstance(allocation_raw, str):
        allocation_raw = json.loads(allocation_raw)
    if not isinstance(allocation_raw, dict):
        allocation_raw = {}

    t = row["time"]
    computed_at = t if hasattr(t, "tzinfo") and t.tzinfo is not None else t.replace(tzinfo=UTC)

    return PortfolioSnapshotResponse(
        snapshot_date=computed_at.date(),
        total_portfolio_value=Decimal(str(row["total_portfolio"])),
        weighted_daily_return=Decimal(str(row["weighted_return"])),
        combined_drawdown=(
            Decimal(str(row["combined_drawdown"]))
            if row.get("combined_drawdown") is not None
            else None
        ),
        active_strategies=row["active_strategies"],
        allocation={k: Decimal(str(v)) for k, v in allocation_raw.items()},
        computed_at=computed_at,
    )


async def query_latest_snapshot(pool: asyncpg.Pool) -> PortfolioSnapshotResponse | None:
    """Return the most recent ``portfolio_snapshot`` row, or ``None`` if the table is empty.

    Raises:
        ServiceError: If Postgres rejects the query.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_LATEST_SNAPSHOT_SQL)
    except asyncpg.PostgresError as exc:
        raise ServiceError("failed to query portfolio_snapshot") from exc

    if row is None:
        return None
    return _row_to_snapshot_response(dict(row))


async def query_snapshot_by_date(
    pool: asyncpg.Pool, snapshot_date: date
) -> PortfolioSnapshotResponse | None:
    """Return the ``portfolio_snapshot`` row for *snapshot_date*, or ``None``.

    Raises:
        ServiceError: If Postgres rejects the query.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SNAPSHOT_BY_DATE_SQL, snapshot_date)
    except asyncpg.PostgresError as exc:
        raise ServiceError(
            f"failed to query portfolio_snapshot for {snapshot_date.isoformat()}"
        ) from exc

    if row is None:
        return None
    return _row_to_snapshot_response(dict(row))


async def compute_portfolio_equity_curve(
    pool: asyncpg.Pool,
    registry: StrategyRegistry,
) -> list[EquityPoint]:
    """Read the latest equity curve from every active strategy and merge them.

    Args:
        pool: The asyncpg pool for ``db_gateway``.
        registry: The strategy registry loaded at startup.

    Returns:
        The merged equity curve (a list of :class:`EquityPoint`). Empty list
        when no strategies are active or none carry usable equity curves.
    """
    active = registry.active_strategies()
    if not active:
        return []

    active_ids = [cfg.id for cfg in active]
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_LATEST_PER_STRATEGY_FOR_EQUITY_SQL, active_ids)
    except asyncpg.PostgresError as exc:
        raise ServiceError("failed to query daily_performance for equity curves") from exc

    curves: dict[str, list[EquityPoint]] = {}
    for row in rows:
        r = dict(row)
        curve = _extract_equity_curve(r.get("metadata"))
        if curve:
            curves[r["strategy_id"]] = curve

    if not curves:
        return []

    weights: dict[str, float] = {
        sid: float(cfg.capital_weight) for sid, cfg in zip(active_ids, active, strict=True)
    }

    return merge_equity_curves(curves, weights)
