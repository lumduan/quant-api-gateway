"""Write ``portfolio_snapshot`` rows when every active strategy has reported.

The writer is invoked inline after each successful ingest. It is a no-op
unless every active strategy in the registry has a ``daily_performance`` row
for "today" (UTC date), at which point a single ``portfolio_snapshot`` row is
upserted for that date.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any

import asyncpg

from src.schemas.registry import StrategyConfig, StrategyRegistry
from src.schemas.strategy import EquityPoint
from src.services.aggregator import calculate_combined_drawdown
from src.services.cache_invalidator import (
    invalidate_overall_cache,
    invalidate_strategy_cache,
)
from src.services.errors import ServiceError

logger = logging.getLogger(__name__)


_SELECT_TODAY_SQL = """
SELECT DISTINCT ON (strategy_id)
    strategy_id, total_value, daily_return, metadata
FROM daily_performance
WHERE time::date = $1 AND strategy_id = ANY($2::text[])
ORDER BY strategy_id, time DESC
"""

_UPSERT_SQL = """
INSERT INTO portfolio_snapshot (
    time, total_portfolio, weighted_return, combined_drawdown,
    active_strategies, allocation
) VALUES ($1, $2, $3, $4, $5, $6::jsonb)
ON CONFLICT (time) DO UPDATE SET
    total_portfolio = EXCLUDED.total_portfolio,
    weighted_return = EXCLUDED.weighted_return,
    combined_drawdown = EXCLUDED.combined_drawdown,
    active_strategies = EXCLUDED.active_strategies,
    allocation = EXCLUDED.allocation
"""


@dataclass(frozen=True)
class SnapshotAggregates:
    """Aggregates computed from a complete daily round."""

    total_portfolio: float
    weighted_return: float
    combined_drawdown: float | None
    active_strategies: int
    allocation: dict[str, float]


def _extract_equity_curve(metadata: Any) -> list[EquityPoint]:
    """Extract the ``equity_curve`` list from a ``daily_performance.metadata`` blob.

    ``asyncpg`` returns JSONB as ``str`` by default; tests sometimes mock it as
    a Python ``dict``. Both shapes are tolerated. Missing / malformed payloads
    return an empty list (the aggregator silently ignores empty curves).
    """
    if metadata is None:
        return []
    if isinstance(metadata, str):
        try:
            payload = json.loads(metadata)
        except json.JSONDecodeError:
            return []
    elif isinstance(metadata, dict):
        payload = metadata
    else:
        return []

    raw_curve = payload.get("equity_curve") if isinstance(payload, dict) else None
    if not isinstance(raw_curve, list):
        return []

    points: list[EquityPoint] = []
    for entry in raw_curve:
        if not isinstance(entry, dict):
            continue
        date = entry.get("date")
        value = entry.get("value")
        if not isinstance(date, str) or value is None:
            continue
        try:
            points.append(EquityPoint(date=date, value=Decimal(str(value))))
        except (ValueError, ArithmeticError):
            continue
    return points


_CURVE_FROM_ROWS_SQL = """
SELECT time::date AS date, total_value
FROM daily_performance
WHERE strategy_id = $1
ORDER BY time ASC
"""


async def build_equity_curve_from_rows(
    conn: asyncpg.Connection,
    strategy_id: str,
) -> list[EquityPoint]:
    """Build an equity curve from ``daily_performance`` rows.

    Fallback when ``metadata.equity_curve`` is missing — queries every row
    for *strategy_id* ordered by time and returns ``EquityPoint`` objects
    built from ``time::date`` and ``total_value``.
    """
    rows = await conn.fetch(_CURVE_FROM_ROWS_SQL, strategy_id)
    points: list[EquityPoint] = []
    for row in rows:
        try:
            points.append(
                EquityPoint(date=str(row["date"]), value=Decimal(str(row["total_value"])))
            )
        except (ValueError, ArithmeticError):
            continue
    return points



def _compute_aggregates(
    rows: Sequence[dict[str, Any]],
    active: Sequence[StrategyConfig],
) -> SnapshotAggregates:
    """Compute portfolio aggregates from the latest per-strategy daily rows.

    Args:
        rows: One row per active strategy, each with keys ``strategy_id``,
            ``total_value``, ``daily_return``, and ``metadata`` (JSONB or
            already-parsed dict containing the strategy's full
            ``equity_curve``).
        active: The active registry entries.

    Returns:
        A :class:`SnapshotAggregates`. ``combined_drawdown`` is the float
        returned by :func:`calculate_combined_drawdown` over the per-strategy
        equity curves extracted from ``metadata``; it is ``None`` when no row
        carries a usable curve (graceful degradation per ROADMAP §4.2).
    """
    weights: dict[str, Decimal] = {cfg.id: cfg.capital_weight for cfg in active}
    total_weight = sum(weights.values(), start=Decimal(0))

    rows_by_id = {row["strategy_id"]: row for row in rows}
    total_portfolio = sum(float(rows_by_id[sid]["total_value"]) for sid in weights)

    if total_weight > 0:
        weighted_return = sum(
            float(rows_by_id[sid]["daily_return"]) * float(weight)
            for sid, weight in weights.items()
            if sid in rows_by_id
        ) / float(total_weight)
        allocation = {sid: float(weight) / float(total_weight) for sid, weight in weights.items()}
    else:
        weighted_return = 0.0
        allocation = {sid: 0.0 for sid in weights}

    curves: dict[str, list[EquityPoint]] = {}
    for sid in weights:
        row = rows_by_id.get(sid)
        if row is None:
            continue
        curve = _extract_equity_curve(row.get("metadata"))
        if curve:
            curves[sid] = curve

    if curves:
        float_weights = {sid: float(weight) for sid, weight in weights.items()}
        combined_drawdown: float | None = calculate_combined_drawdown(curves, float_weights)
    else:
        logger.info("snapshot writer: combined_drawdown unavailable — no equity curves in metadata")
        combined_drawdown = None

    return SnapshotAggregates(
        total_portfolio=total_portfolio,
        weighted_return=weighted_return,
        combined_drawdown=combined_drawdown,
        active_strategies=len(active),
        allocation=allocation,
    )


async def maybe_write_snapshot(
    *,
    pool: asyncpg.Pool,
    registry: StrategyRegistry,
    now: datetime | None = None,
) -> bool:
    """Write a portfolio snapshot if the current day's round is complete.

    Args:
        pool: The asyncpg pool for ``db_gateway``.
        registry: The strategy registry (the loaded ``strategies.json``).
        now: The current moment in UTC. Defaults to :func:`datetime.now` (UTC).
            Tests pass a fixed value to assert on the bucketed date.

    Returns:
        ``True`` if a snapshot row was upserted; ``False`` if the round is
        incomplete and the writer did nothing.

    Raises:
        ServiceError: If Postgres rejects the SELECT or UPSERT.
    """
    if now is None:
        now = datetime.now(UTC)
    today = now.date()
    bucket = datetime.combine(today, time.min, tzinfo=UTC)

    active = registry.active_strategies()
    if not active:
        logger.info("snapshot writer: registry has no active strategies; skipping")
        return False
    active_ids = [cfg.id for cfg in active]

    try:
        async with pool.acquire() as conn:
            raw_rows = await conn.fetch(_SELECT_TODAY_SQL, today, active_ids)
    except asyncpg.PostgresError as exc:
        raise ServiceError("failed to read daily_performance for snapshot") from exc

    rows = [dict(r) for r in raw_rows]
    if len(rows) < len(active_ids):
        logger.info(
            "snapshot writer: round incomplete (%d/%d reported); skipping",
            len(rows),
            len(active_ids),
        )
        return False

    agg = _compute_aggregates(rows, active)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                bucket,
                agg.total_portfolio,
                agg.weighted_return,
                agg.combined_drawdown,
                agg.active_strategies,
                json.dumps(agg.allocation),
            )
    except asyncpg.PostgresError as exc:
        raise ServiceError("failed to upsert portfolio_snapshot") from exc
    logger.info(
        "portfolio_snapshot upserted time=%s total=%.2f active=%d",
        bucket.isoformat(),
        agg.total_portfolio,
        agg.active_strategies,
    )
    try:
        await invalidate_overall_cache()
        for cfg in active:
            await invalidate_strategy_cache(cfg.id)
    except Exception:
        logger.exception("cache invalidation failed after snapshot upsert")
    return True
