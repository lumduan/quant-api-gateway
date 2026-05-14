"""Persist a ``StrategyPayload`` into ``db_gateway.daily_performance``.

The mapping decisions for Phase 3 are documented in
``docs/plans/phase_3_strategy_ingestion/phase_3_strategy_ingestion.md`` §"Design
decisions". Briefly:

* ``daily_return`` is computed as ``daily_pnl / total_value`` (fractional).
* ``cumulative_return`` is derived from the equity curve when it has ≥ 2 points.
* Raw ``daily_pnl`` plus the equity curve, positions count, type, and extension
  data are preserved inside the ``metadata`` JSONB blob.
"""

import json
import logging
from decimal import Decimal
from typing import Any

import asyncpg

from src.schemas.strategy import StrategyPayload
from src.services.errors import IngestionPersistError

logger = logging.getLogger(__name__)

_UPSERT_SQL = """
INSERT INTO daily_performance (
    time, strategy_id, daily_return, cumulative_return, total_value,
    cash_balance, max_drawdown, sharpe_ratio, metadata
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
ON CONFLICT (time, strategy_id) DO UPDATE SET
    daily_return = EXCLUDED.daily_return,
    cumulative_return = EXCLUDED.cumulative_return,
    total_value = EXCLUDED.total_value,
    cash_balance = EXCLUDED.cash_balance,
    max_drawdown = EXCLUDED.max_drawdown,
    sharpe_ratio = EXCLUDED.sharpe_ratio,
    metadata = EXCLUDED.metadata
"""


def _payload_to_row(payload: StrategyPayload) -> dict[str, Any]:
    """Map a validated ``StrategyPayload`` into ``daily_performance`` columns.

    Note: ``metadata`` is typed ``dict[str, Any]`` because the JSONB blob's shape
    is intentionally heterogeneous — we preserve every field the payload carried
    that doesn't have a dedicated column.

    Args:
        payload: The validated input payload from a Strategy Service.

    Returns:
        A dict whose keys match the SQL parameters in :data:`_UPSERT_SQL`.
    """
    metrics = payload.performance_metrics
    exposure = payload.current_exposure
    metadata = payload.strategy_metadata

    total_value = float(exposure.total_value)
    daily_pnl = float(metrics.daily_pnl)
    daily_return = daily_pnl / total_value if total_value > 0 else 0.0

    cumulative_return: float | None
    if len(metrics.equity_curve) >= 2:
        first = metrics.equity_curve[0].value
        last = metrics.equity_curve[-1].value
        cumulative_return = float(last / first) - 1.0 if first > 0 else None
    else:
        cumulative_return = None

    metadata_blob: dict[str, Any] = {
        "type": metadata.type,
        "positions_count": exposure.positions_count,
        "daily_pnl": str(metrics.daily_pnl),
        "equity_curve": [{"date": p.date, "value": str(p.value)} for p in metrics.equity_curve],
        "extended_data": dict(payload.extended_data),
    }

    return {
        "time": metadata.last_updated,
        "strategy_id": metadata.id,
        "daily_return": daily_return,
        "cumulative_return": cumulative_return,
        "total_value": total_value,
        "cash_balance": float(exposure.cash_balance),
        "max_drawdown": float(metrics.max_drawdown),
        "sharpe_ratio": float(metrics.sharpe_ratio),
        "metadata_json": json.dumps(metadata_blob, default=_decimal_to_str),
    }


def _decimal_to_str(obj: Any) -> str:
    """``json.dumps`` ``default=`` helper — preserves ``Decimal`` losslessly as a string."""
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"object of type {type(obj).__name__} is not JSON serializable")


async def persist_daily_report(
    payload: StrategyPayload,
    *,
    pool: asyncpg.Pool,
) -> None:
    """Upsert ``payload`` into ``daily_performance``.

    Args:
        payload: The validated input payload from a Strategy Service.
        pool: The asyncpg pool for ``db_gateway``.

    Raises:
        IngestionPersistError: If the database rejects the write.
    """
    row = _payload_to_row(payload)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                row["time"],
                row["strategy_id"],
                row["daily_return"],
                row["cumulative_return"],
                row["total_value"],
                row["cash_balance"],
                row["max_drawdown"],
                row["sharpe_ratio"],
                row["metadata_json"],
            )
    except asyncpg.PostgresError as exc:
        logger.exception("daily_performance upsert failed for %s", row["strategy_id"])
        raise IngestionPersistError(
            f"failed to persist daily_performance for {row['strategy_id']}"
        ) from exc
    logger.info(
        "daily_performance upserted strategy_id=%s time=%s daily_return=%.6f",
        row["strategy_id"],
        row["time"].isoformat(),
        row["daily_return"],
    )
