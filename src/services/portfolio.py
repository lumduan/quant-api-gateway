"""Business logic for querying portfolio snapshots and computing merged equity curves."""

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from src.schemas.gateway import (
    MetricItem,
    PortfolioMetricsResponse,
    PortfolioSnapshotResponse,
)
from src.schemas.registry import StrategyRegistry
from src.schemas.strategy import EquityPoint
from src.services.aggregator import merge_equity_curves
from src.services.errors import ServiceError
from src.services.snapshot_writer import _extract_equity_curve, build_equity_curve_from_rows
from src.utils.formatting import format_currency, format_delta_number, format_percentage

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

_PREVIOUS_SNAPSHOT_SQL = """
SELECT time, total_portfolio, weighted_return, combined_drawdown,
       active_strategies, allocation
FROM portfolio_snapshot
WHERE time::date < $1
ORDER BY time DESC
LIMIT 1
"""

_LATEST_PER_STRATEGY_FOR_EQUITY_SQL = """
SELECT DISTINCT ON (strategy_id)
    strategy_id, metadata
FROM daily_performance
WHERE strategy_id = ANY($1::text[])
ORDER BY strategy_id, time DESC
"""


_TWO_PLACES = Decimal("0.01")
_FOUR_PLACES = Decimal("0.0001")
_SIX_PLACES = Decimal("0.000001")


def _to_decimal(value: object, quant: Decimal | None = None) -> Decimal:
    """Coerce *value* to Decimal and optionally quantize to *quant* places."""
    d = Decimal(str(value))
    if quant is not None:
        d = d.quantize(quant)
    return d


def _row_to_snapshot_response(row: dict[str, Any]) -> PortfolioSnapshotResponse:
    """Convert a ``portfolio_snapshot`` row into a :class:`PortfolioSnapshotResponse`.

    Values are quantized to match the Pydantic model's ``decimal_places``
    constraints, preventing validation errors from high-precision float data
    written by the snapshot writer.
    """
    allocation_raw = row.get("allocation")
    if isinstance(allocation_raw, str):
        allocation_raw = json.loads(allocation_raw)
    if not isinstance(allocation_raw, dict):
        allocation_raw = {}

    t = row["time"]
    computed_at = t if hasattr(t, "tzinfo") and t.tzinfo is not None else t.replace(tzinfo=UTC)

    return PortfolioSnapshotResponse(
        snapshot_date=computed_at.date(),
        total_portfolio_value=_to_decimal(row["total_portfolio"], _TWO_PLACES),
        weighted_daily_return=_to_decimal(row["weighted_return"], _SIX_PLACES),
        combined_drawdown=(
            _to_decimal(row["combined_drawdown"], _FOUR_PLACES)
            if row.get("combined_drawdown") is not None
            else None
        ),
        active_strategies=row["active_strategies"],
        allocation={k: _to_decimal(v, _FOUR_PLACES) for k, v in allocation_raw.items()},
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


async def query_previous_snapshot(
    pool: asyncpg.Pool, before_date: date
) -> PortfolioSnapshotResponse | None:
    """Return the most recent ``portfolio_snapshot`` strictly before *before_date*.

    Used to compute day-over-day deltas; the previous row may be more than one
    calendar day older when snapshots are sparse.

    Raises:
        ServiceError: If Postgres rejects the query.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_PREVIOUS_SNAPSHOT_SQL, before_date)
    except asyncpg.PostgresError as exc:
        raise ServiceError(
            f"failed to query previous portfolio_snapshot before {before_date.isoformat()}"
        ) from exc

    if row is None:
        return None
    return _row_to_snapshot_response(dict(row))


_PCT_TO_POINTS = Decimal(100)


def build_metrics_response(
    current: PortfolioSnapshotResponse,
    previous: PortfolioSnapshotResponse | None,
) -> PortfolioMetricsResponse:
    """Compose the three Metric-widget items for *current* (vs *previous* for delta).

    Output shape matches OpenBB's Metric widget verbatim: plain value cell
    (units in `value`, no arrows) and a small delta cell (arrow + raw number,
    no units). Metrics are emitted in fixed order so widget configs can rely
    on positional indexes. Delta is ``None`` when no previous snapshot exists
    or the source field is null on either side. Percentage deltas are pre-
    scaled to percentage points so the widget shows ``↑ 0.12`` not ``↑ 0.0012``.
    """
    daily_return_delta = ""
    if previous is not None:
        daily_return_delta = format_delta_number(
            (current.weighted_daily_return - previous.weighted_daily_return) * _PCT_TO_POINTS
        )

    if current.combined_drawdown is None:
        drawdown_value = "N/A"
        drawdown_delta = ""
    else:
        drawdown_value = format_percentage(current.combined_drawdown, use_arrows=False)
        drawdown_delta = ""
        if previous is not None and previous.combined_drawdown is not None:
            drawdown_delta = format_delta_number(
                (current.combined_drawdown - previous.combined_drawdown) * _PCT_TO_POINTS
            )

    value_delta = ""
    if previous is not None:
        value_delta = format_delta_number(
            current.total_portfolio_value - previous.total_portfolio_value
        )

    metrics = [
        MetricItem(
            label="Daily Return",
            value=format_percentage(current.weighted_daily_return, use_arrows=False),
            delta=daily_return_delta,
        ),
        MetricItem(
            label="Portfolio Drawdown",
            value=drawdown_value,
            delta=drawdown_delta,
        ),
        MetricItem(
            label="Total Portfolio Value",
            value=format_currency(current.total_portfolio_value),
            delta=value_delta,
        ),
    ]

    return PortfolioMetricsResponse(
        snapshot_date=current.snapshot_date,
        metrics=metrics,
        computed_at=datetime.now(UTC),
    )


async def compute_portfolio_equity_curve(
    pool: asyncpg.Pool,
    registry: StrategyRegistry,
    normalize: bool = True,
) -> list[EquityPoint]:
    """Read the latest equity curve from every active strategy and merge them.

    Args:
        pool: The asyncpg pool for ``db_gateway``.
        registry: The strategy registry loaded at startup.
        normalize: When ``True`` (default), normalise each input curve to
            base 100 before merging. When ``False``, use raw values.

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

            curves: dict[str, list[EquityPoint]] = {}
            for row in rows:
                r = dict(row)
                curve = _extract_equity_curve(r.get("metadata"))
                if curve:
                    curves[r["strategy_id"]] = curve
                else:
                    # Fallback: reconstruct from daily_performance rows
                    fallback = await build_equity_curve_from_rows(conn, r["strategy_id"])
                    if fallback:
                        curves[r["strategy_id"]] = fallback
    except asyncpg.PostgresError as exc:
        raise ServiceError("failed to query daily_performance for equity curves") from exc

    if not curves:
        return []

    weights: dict[str, float] = {
        sid: float(cfg.capital_weight) for sid, cfg in zip(active_ids, active, strict=True)
    }

    return merge_equity_curves(curves, weights, normalize=normalize)
