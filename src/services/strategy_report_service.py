"""Read and write helpers for the strategy-report endpoints.

* :func:`persist_report` UPSERTs a parsed :class:`StrategyReport` into
  ``db_gateway.strategy_report_snapshot``. The caller passes an existing
  asyncpg connection so the write can participate in the ingestion
  transaction (so a report-write failure rolls back the day's
  ``daily_performance`` insert).
* :func:`get_latest_report` and :func:`get_report_for_date` read the same
  hypertable and return a typed :class:`StrategyReportResponse`.
* :func:`list_trades` and :func:`get_benchmark_curve` read from
  ``db_csm_set`` via the read-only pool exposed by
  :mod:`src.db.csm_set_postgres`.

All public functions raise typed exceptions from :mod:`src.services.errors`
(``ServiceError`` for I/O failures, ``StrategyReportNotFoundError`` for
missing data) — never bare exceptions.
"""

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from src.schemas.strategy_report import (
    BenchmarkPoint,
    StrategyReport,
    StrategyReportResponse,
    TradeLogEntry,
    TradeLogPage,
)
from src.services.errors import ServiceError, StrategyReportNotFoundError

logger = logging.getLogger(__name__)


_UPSERT_SNAPSHOT_SQL = """
INSERT INTO strategy_report_snapshot (time, strategy_id, report)
VALUES ($1, $2, $3::jsonb)
ON CONFLICT (time, strategy_id) DO UPDATE SET
    report = EXCLUDED.report,
    computed_at = now()
"""

_LATEST_SNAPSHOT_SQL = """
SELECT time, report, computed_at
FROM strategy_report_snapshot
WHERE strategy_id = $1
ORDER BY time DESC
LIMIT 1
"""

_SNAPSHOT_BY_DATE_SQL = """
SELECT time, report, computed_at
FROM strategy_report_snapshot
WHERE strategy_id = $1 AND time::date = $2
ORDER BY time DESC
LIMIT 1
"""

_TRADES_COUNT_SQL = """
SELECT count(*)
FROM trade_history
WHERE strategy_id = $1
  AND ($2::timestamptz IS NULL OR time >= $2)
  AND ($3::timestamptz IS NULL OR time <  $3)
  AND side IN ('LONG', 'SHORT')
"""

_TRADES_PAGE_SQL = """
SELECT
    time,
    symbol,
    side,
    quantity,
    COALESCE(entry_price, price)   AS entry_price,
    COALESCE(exit_price,  price)   AS exit_price,
    COALESCE(realized_pnl, 0)      AS realized_pnl,
    COALESCE(duration_bars, 0)     AS duration_bars,
    COALESCE(commission, 0)        AS commission
FROM trade_history
WHERE strategy_id = $1
  AND ($2::timestamptz IS NULL OR time >= $2)
  AND ($3::timestamptz IS NULL OR time <  $3)
  AND side IN ('LONG', 'SHORT')
ORDER BY time DESC
LIMIT $4 OFFSET $5
"""

_BENCHMARK_SQL = """
SELECT time, equity
FROM benchmark_equity_curve
WHERE strategy_id = $1
  AND ($2::timestamptz IS NULL OR time >= $2)
  AND ($3::timestamptz IS NULL OR time <  $3)
ORDER BY time ASC
"""


def _as_utc(value: datetime) -> datetime:
    """Coerce an asyncpg-returned ``datetime`` to a tz-aware UTC value.

    asyncpg returns ``TIMESTAMPTZ`` values as tz-aware ``datetime``s already,
    but the test layer sometimes mocks rows as naive ``datetime``s. We tolerate
    both: a naive value is assumed UTC and replaced with a UTC-aware copy.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _decode_report(raw: Any, *, strategy_id: str) -> StrategyReport:
    """Parse a JSONB column into a :class:`StrategyReport`.

    asyncpg returns ``JSONB`` as ``str`` by default; some test fixtures pass
    an already-parsed ``dict``. Both shapes are tolerated.

    Raises:
        ServiceError: On JSON or model-validation failure (a stored row that
            was once valid no longer parses → operational issue, not 404).
    """
    if isinstance(raw, dict):
        payload: Any = raw
    elif isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ServiceError(
                f"corrupt strategy_report_snapshot row for {strategy_id!r}: invalid JSON"
            ) from exc
    else:
        raise ServiceError(
            f"unexpected report column type for {strategy_id!r}: {type(raw).__name__}"
        )
    try:
        return StrategyReport.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — wrap as ServiceError
        raise ServiceError(
            f"strategy_report_snapshot row for {strategy_id!r} failed validation"
        ) from exc


async def persist_report(
    conn: asyncpg.Connection,
    *,
    strategy_id: str,
    report: StrategyReport,
    time: datetime,
) -> None:
    """UPSERT ``report`` into ``strategy_report_snapshot``.

    Args:
        conn: An open asyncpg connection (caller-supplied so the write can
            participate in an outer transaction).
        strategy_id: The owning strategy identifier.
        report: The validated :class:`StrategyReport` payload.
        time: The reporting timestamp (UTC). The unique constraint is on
            ``(time, strategy_id)`` so re-posting the same instant is safe.

    Raises:
        ServiceError: If the Postgres write fails.

    Example:
        >>> await persist_report(conn, strategy_id="csm-set-01",
        ...                      report=parsed, time=datetime.now(UTC))
    """
    payload = report.model_dump_json()
    try:
        await conn.execute(_UPSERT_SNAPSHOT_SQL, time, strategy_id, payload)
    except asyncpg.PostgresError as exc:
        logger.exception(
            "strategy_report_snapshot upsert failed strategy_id=%s time=%s",
            strategy_id,
            time.isoformat(),
        )
        raise ServiceError(
            f"failed to persist strategy_report_snapshot for {strategy_id!r}"
        ) from exc
    logger.info(
        "strategy_report_snapshot upserted strategy_id=%s time=%s bytes=%d",
        strategy_id,
        time.isoformat(),
        len(payload),
    )


async def get_latest_report(pool: asyncpg.Pool, *, strategy_id: str) -> StrategyReportResponse:
    """Return the most recent snapshot for *strategy_id*.

    Raises:
        ServiceError: If the database query fails.
        StrategyReportNotFoundError: If no snapshot exists for the strategy.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_LATEST_SNAPSHOT_SQL, strategy_id)
    except asyncpg.PostgresError as exc:
        raise ServiceError(f"failed to read strategy_report_snapshot for {strategy_id!r}") from exc

    if row is None:
        raise StrategyReportNotFoundError(strategy_id)
    return _row_to_response(dict(row), strategy_id=strategy_id)


async def get_report_for_date(
    pool: asyncpg.Pool, *, strategy_id: str, target_date: date
) -> StrategyReportResponse:
    """Return the snapshot for *strategy_id* on *target_date*.

    Raises:
        ServiceError: If the database query fails.
        StrategyReportNotFoundError: If no snapshot exists for that date.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SNAPSHOT_BY_DATE_SQL, strategy_id, target_date)
    except asyncpg.PostgresError as exc:
        raise ServiceError(
            f"failed to read strategy_report_snapshot for {strategy_id!r} "
            f"on {target_date.isoformat()}"
        ) from exc

    if row is None:
        raise StrategyReportNotFoundError(strategy_id, date=target_date.isoformat())
    return _row_to_response(dict(row), strategy_id=strategy_id)


def _row_to_response(row: dict[str, Any], *, strategy_id: str) -> StrategyReportResponse:
    """Map a ``strategy_report_snapshot`` row into a typed response."""
    report = _decode_report(row["report"], strategy_id=strategy_id)
    return StrategyReportResponse(
        strategy_id=strategy_id,
        as_of=_as_utc(row["time"]),
        report=report,
        computed_at=_as_utc(row["computed_at"]),
    )


def _date_to_utc(value: date | None) -> datetime | None:
    """Coerce an optional date filter to a midnight-UTC ``datetime``.

    Bounds are half-open: callers expect ``from_date <= time < to_date + 1d``.
    """
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


async def list_trades(
    pool: asyncpg.Pool,
    *,
    strategy_id: str,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> TradeLogPage:
    """Return a paginated trade log for *strategy_id* from ``db_csm_set``.

    Args:
        pool: The ``db_csm_set`` read-only pool.
        strategy_id: Strategy identifier.
        from_date: Inclusive lower bound on ``time`` (UTC). ``None`` ⇒ no lower bound.
        to_date: Inclusive upper bound on ``time`` (UTC). ``None`` ⇒ no upper bound.
            The upper bound is implemented as ``time < to_date + 1 day`` so
            the page includes every trade on ``to_date``.
        limit: Page size (1..1000).
        offset: Page offset (≥ 0).

    Returns:
        A :class:`TradeLogPage`.

    Raises:
        ServiceError: If the database read fails.
    """
    if limit < 1 or limit > 1000:
        raise ServiceError(f"limit out of range: {limit}")
    if offset < 0:
        raise ServiceError(f"offset out of range: {offset}")

    lower = _date_to_utc(from_date)
    upper: datetime | None
    if to_date is None:
        upper = None
    else:
        # Half-open upper bound — include trades on `to_date`.
        upper = datetime(to_date.year, to_date.month, to_date.day, tzinfo=UTC)
        upper = datetime.fromtimestamp(upper.timestamp() + 86400, tz=UTC)

    try:
        async with pool.acquire() as conn:
            total = await conn.fetchval(_TRADES_COUNT_SQL, strategy_id, lower, upper)
            rows = await conn.fetch(_TRADES_PAGE_SQL, strategy_id, lower, upper, limit, offset)
    except asyncpg.PostgresError as exc:
        raise ServiceError(f"failed to read trade_history for {strategy_id!r}") from exc

    items = [_row_to_trade(dict(r)) for r in rows]
    return TradeLogPage(
        items=items,
        total=int(total) if total is not None else 0,
        limit=limit,
        offset=offset,
    )


def _row_to_trade(row: dict[str, Any]) -> TradeLogEntry:
    """Map a ``trade_history`` row into a :class:`TradeLogEntry`.

    The trade_history table does not (yet) record separate entry/exit
    timestamps — each row is one realised trade with a single ``time``
    column. Until csm-set widens the schema, the gateway exposes ``time``
    as both ``entry_time`` and ``exit_time``; the dashboard renders that as
    a single timestamp.
    """
    t = _as_utc(row["time"])
    return TradeLogEntry(
        entry_time=t,
        exit_time=t,
        symbol=str(row["symbol"]),
        side=row["side"],
        qty=Decimal(str(row["quantity"])),
        entry_price=Decimal(str(row["entry_price"])),
        exit_price=Decimal(str(row["exit_price"])),
        realized_pnl=Decimal(str(row["realized_pnl"])),
        duration_bars=int(row["duration_bars"]),
        commission=Decimal(str(row["commission"])),
    )


async def get_benchmark_curve(
    pool: asyncpg.Pool,
    *,
    strategy_id: str,
    from_date: date | None = None,
    to_date: date | None = None,
    normalize: bool = False,
) -> list[BenchmarkPoint]:
    """Return the benchmark equity curve for *strategy_id*.

    Args:
        pool: The ``db_csm_set`` read-only pool.
        strategy_id: Strategy identifier.
        from_date: Inclusive lower bound on ``time`` (UTC). ``None`` ⇒ no
            lower bound.
        to_date: Inclusive upper bound on ``time`` (UTC). ``None`` ⇒ no
            upper bound (half-open, see :func:`list_trades`).
        normalize: When ``True``, scales each ``equity`` to base-100
            relative to the first sample. When ``False`` (default), returns
            raw values.

    Returns:
        A list of :class:`BenchmarkPoint` ordered ascending by ``time``.

    Raises:
        ServiceError: If the database read fails.
    """
    lower = _date_to_utc(from_date)
    upper: datetime | None
    if to_date is None:
        upper = None
    else:
        upper = datetime(to_date.year, to_date.month, to_date.day, tzinfo=UTC)
        upper = datetime.fromtimestamp(upper.timestamp() + 86400, tz=UTC)

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_BENCHMARK_SQL, strategy_id, lower, upper)
    except asyncpg.PostgresError as exc:
        raise ServiceError(f"failed to read benchmark_equity_curve for {strategy_id!r}") from exc

    if not rows:
        return []

    points: list[BenchmarkPoint] = [
        BenchmarkPoint(date=_as_utc(r["time"]), value=Decimal(str(r["equity"]))) for r in rows
    ]
    if normalize:
        base = points[0].value
        if base != Decimal(0):
            points = [
                BenchmarkPoint(date=p.date, value=(p.value / base) * Decimal(100)) for p in points
            ]
    return points
