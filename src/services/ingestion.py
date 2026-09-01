"""Persist a ``StrategyPayload`` into ``db_gateway.daily_performance``.

The mapping decisions for Phase 3 are documented in
``docs/plans/phase_3_strategy_ingestion/phase_3_strategy_ingestion.md`` §"Design
decisions". Briefly:

* ``daily_return`` is ``daily_pnl / PRIOR value`` (fractional), the prior value
  being the payload equity curve's second-to-last point. Phase 3 originally
  divided by ``total_value`` — today's NAV — which is systematically biased and
  one-directional; see :func:`_daily_return`. The legacy basis survives only as
  a logged fallback for payloads carrying fewer than two equity points.
* ``cumulative_return`` is derived from the equity curve when it has ≥ 2 points.
* Raw ``daily_pnl`` plus the equity curve, positions count, type, and extension
  data are preserved inside the ``metadata`` JSONB blob.
"""

import json
import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import asyncpg

from src.schemas.strategy import EquityPoint, StrategyPayload
from src.services.errors import IngestionPersistError
from src.services.strategy_report_service import persist_report

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


def _daily_return(
    *,
    daily_pnl: float,
    total_value: float,
    equity_curve: Sequence[EquityPoint],
    strategy_id: str,
) -> float:
    """Return the day's fractional return: ``daily_pnl / PRIOR value``.

    A return is a change measured against **what you started the period with**.
    Phase 3 chose ``daily_pnl / total_value`` — dividing by the value you *ended*
    with — because it made the Phase 4 aggregator's units line up. The units were
    never the problem; the denominator was. Using today's value makes the quantity
    systematically biased and **one-directional**: it understates every gain and
    overstates every loss, because the denominator moves with the numerator.

    Confirmed against csm-set on 2026-09-01 — stored ``-0.03426456`` where the
    strategy's own engine computed ``-0.03312940`` for the same session. The
    strategy was right; three months of daily logs carried the discrepancy as an
    open defect before it was localised to this function.

    The prior value comes from the payload's own equity curve, which is already
    the source ``cumulative_return`` trusts a few lines below — so this needs no
    database lookup and no signature change.

    ``daily_pnl`` stays the numerator rather than deriving the whole ratio from
    the curve (``curve[-1] / curve[-2] - 1``). On a capital-injection day the
    curve steps by the injected amount, and a ratio taken straight off it books
    that deposit as a *return*. ``daily_pnl`` is the strategy's own statement of
    what the book earned, so it keeps flows out of the numerator.

    Args:
        daily_pnl: The strategy's reported P/L for the session.
        total_value: Today's NAV — used only by the degraded fallback.
        equity_curve: The payload's NAV series; ``[-2]`` is the prior value.
        strategy_id: Named in the warning when the fallback is taken.

    Returns:
        The fractional daily return, or ``0.0`` when neither basis is available.
    """
    if len(equity_curve) >= 2:
        prior_value = float(equity_curve[-2].value)
        if prior_value > 0:
            return daily_pnl / prior_value

    # No prior observation in the payload, so a daily return is not defined from
    # it. Fall back to the legacy basis rather than silently emitting 0.0 — but
    # say so, because the value that lands is NOT the same quantity as above and
    # nothing downstream can tell the two apart once it is a float in a column.
    if total_value > 0:
        logger.warning(
            "daily_return for strategy_id=%s fell back to the LEGACY basis "
            "(daily_pnl / TODAY's total_value): the payload's equity_curve has %d point(s), "
            "so no prior value is available. This value is biased and not comparable with "
            "rows computed against the prior value.",
            strategy_id,
            len(equity_curve),
        )
        return daily_pnl / total_value
    return 0.0


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
    daily_return = _daily_return(
        daily_pnl=daily_pnl,
        total_value=total_value,
        equity_curve=metrics.equity_curve,
        strategy_id=metadata.id,
    )

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
    """Upsert ``payload`` into ``daily_performance`` (and, when present, the report).

    The ``daily_performance`` UPSERT and the optional
    ``strategy_report_snapshot`` UPSERT run inside a single
    ``conn.transaction()`` so a report-write failure rolls back the day's
    performance insert. This keeps the two rows atomic per strategy per day,
    matching the umbrella feature roadmap's "atomic per strategy per day"
    contract.

    Args:
        payload: The validated input payload from a Strategy Service. If
            :attr:`StrategyPayload.parsed_report` is non-``None``, the
            parsed report is also UPSERTed.
        pool: The asyncpg pool for ``db_gateway``.

    Raises:
        IngestionPersistError: If either write fails — both rows are rolled
            back in that case.
    """
    row = _payload_to_row(payload)
    report = payload.parsed_report
    try:
        async with pool.acquire() as conn, conn.transaction():
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
            if report is not None:
                await persist_report(
                    conn,
                    strategy_id=row["strategy_id"],
                    report=report,
                    time=row["time"],
                )
    except asyncpg.PostgresError as exc:
        logger.exception("daily_performance upsert failed for %s", row["strategy_id"])
        raise IngestionPersistError(
            f"failed to persist daily_performance for {row['strategy_id']}"
        ) from exc
    except IngestionPersistError:
        raise
    except Exception as exc:
        # persist_report wraps PostgresError in ServiceError — surface that
        # too so the route layer can return a clean 500.
        logger.exception("strategy_report_snapshot upsert failed for %s", row["strategy_id"])
        raise IngestionPersistError(
            f"failed to persist strategy_report for {row['strategy_id']}"
        ) from exc
    logger.info(
        "daily_performance upserted strategy_id=%s time=%s daily_return=%.6f report=%s",
        row["strategy_id"],
        row["time"].isoformat(),
        row["daily_return"],
        report is not None,
    )
