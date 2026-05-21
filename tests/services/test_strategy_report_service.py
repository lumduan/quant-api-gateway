"""Tests for :mod:`src.services.strategy_report_service`."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from src.schemas.strategy_report import (
    BenchmarkPoint,
    StrategyReport,
)
from src.services import strategy_report_service as svc
from src.services.errors import ServiceError, StrategyReportNotFoundError

from tests.schemas.test_strategy import _report_dict


def _valid_report() -> StrategyReport:
    return StrategyReport.model_validate(_report_dict())


def _row_for(report: StrategyReport, *, t: datetime, computed: datetime) -> dict[str, Any]:
    return {
        "time": t,
        "report": report.model_dump_json(),
        "computed_at": computed,
    }


# -----------------------------------------------------------------------------
# persist_report
# -----------------------------------------------------------------------------


async def test_persist_report_executes_upsert(mock_pool: MagicMock) -> None:
    """Happy path — ``persist_report`` executes the UPSERT SQL with the JSON body."""
    conn = mock_pool._conn
    report = _valid_report()
    when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)

    await svc.persist_report(conn, strategy_id="csm-set-01", report=report, time=when)

    conn.execute.assert_awaited_once()
    args = conn.execute.await_args.args
    assert "strategy_report_snapshot" in args[0]
    assert args[1] == when
    assert args[2] == "csm-set-01"
    # JSON payload
    payload = json.loads(args[3])
    assert payload["headline"]["total_trades"] == 5


async def test_persist_report_wraps_postgres_error(mock_pool: MagicMock) -> None:
    """Postgres errors are wrapped in ``ServiceError``."""
    conn = mock_pool._conn
    conn.execute.side_effect = asyncpg.PostgresError("boom")

    with pytest.raises(ServiceError):
        await svc.persist_report(
            conn,
            strategy_id="csm-set-01",
            report=_valid_report(),
            time=datetime(2026, 5, 20, tzinfo=UTC),
        )


# -----------------------------------------------------------------------------
# get_latest_report / get_report_for_date
# -----------------------------------------------------------------------------


async def test_get_latest_report_returns_response(mock_pool: MagicMock) -> None:
    """Happy path — fetched row maps into ``StrategyReportResponse``."""
    report = _valid_report()
    when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    computed = datetime(2026, 5, 20, 11, 5, tzinfo=UTC)
    mock_pool._conn.fetchrow.return_value = _row_for(report, t=when, computed=computed)

    resp = await svc.get_latest_report(mock_pool, strategy_id="csm-set-01")

    assert resp.strategy_id == "csm-set-01"
    assert resp.as_of == when
    assert resp.computed_at == computed
    assert resp.report.headline.total_trades == report.headline.total_trades


async def test_get_latest_report_naive_db_value_treated_as_utc(
    mock_pool: MagicMock,
) -> None:
    """asyncpg occasionally returns naive datetimes — they are coerced to UTC."""
    report = _valid_report()
    naive_when = datetime(2026, 5, 20, 11, 0)
    naive_computed = datetime(2026, 5, 20, 11, 5)
    mock_pool._conn.fetchrow.return_value = {
        "time": naive_when,
        "report": report.model_dump_json(),
        "computed_at": naive_computed,
    }

    resp = await svc.get_latest_report(mock_pool, strategy_id="csm-set-01")

    assert resp.as_of.tzinfo == UTC
    assert resp.computed_at.tzinfo == UTC


async def test_get_latest_report_accepts_dict_jsonb(mock_pool: MagicMock) -> None:
    """A ``dict`` JSONB column (some drivers return parsed) is accepted."""
    report = _valid_report()
    when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    mock_pool._conn.fetchrow.return_value = {
        "time": when,
        "report": json.loads(report.model_dump_json()),
        "computed_at": when,
    }

    resp = await svc.get_latest_report(mock_pool, strategy_id="csm-set-01")
    assert resp.report.headline.total_trades == report.headline.total_trades


async def test_get_latest_report_404_when_missing(mock_pool: MagicMock) -> None:
    """An empty result raises ``StrategyReportNotFoundError``."""
    mock_pool._conn.fetchrow.return_value = None

    with pytest.raises(StrategyReportNotFoundError) as exc_info:
        await svc.get_latest_report(mock_pool, strategy_id="ghost")

    assert exc_info.value.strategy_id == "ghost"
    assert exc_info.value.date is None


async def test_get_latest_report_db_error_wrapped(mock_pool: MagicMock) -> None:
    """Postgres errors during read surface as ``ServiceError``."""
    mock_pool._conn.fetchrow.side_effect = asyncpg.PostgresError("bad")

    with pytest.raises(ServiceError):
        await svc.get_latest_report(mock_pool, strategy_id="csm-set-01")


async def test_get_report_for_date_happy_path(mock_pool: MagicMock) -> None:
    """Date filter binds correctly and returns the snapshot."""
    when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    report = _valid_report()
    mock_pool._conn.fetchrow.return_value = _row_for(report, t=when, computed=when)

    resp = await svc.get_report_for_date(
        mock_pool, strategy_id="csm-set-01", target_date=date(2026, 5, 20)
    )

    assert resp.strategy_id == "csm-set-01"
    args = mock_pool._conn.fetchrow.await_args.args
    assert args[1] == "csm-set-01"
    assert args[2] == date(2026, 5, 20)


async def test_get_report_for_date_404(mock_pool: MagicMock) -> None:
    """A missing snapshot raises ``StrategyReportNotFoundError`` with the date."""
    mock_pool._conn.fetchrow.return_value = None

    with pytest.raises(StrategyReportNotFoundError) as exc_info:
        await svc.get_report_for_date(
            mock_pool, strategy_id="csm-set-01", target_date=date(2026, 5, 20)
        )
    assert exc_info.value.date == "2026-05-20"


async def test_get_report_for_date_db_error_wrapped(mock_pool: MagicMock) -> None:
    """Postgres errors during date-lookup surface as ``ServiceError``."""
    mock_pool._conn.fetchrow.side_effect = asyncpg.PostgresError("bad")

    with pytest.raises(ServiceError):
        await svc.get_report_for_date(
            mock_pool, strategy_id="csm-set-01", target_date=date(2026, 5, 20)
        )


# -----------------------------------------------------------------------------
# _decode_report failure paths
# -----------------------------------------------------------------------------


async def test_decode_report_invalid_json_wrapped(mock_pool: MagicMock) -> None:
    """A corrupt JSONB column raises ``ServiceError``."""
    when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    mock_pool._conn.fetchrow.return_value = {
        "time": when,
        "report": "not json",
        "computed_at": when,
    }

    with pytest.raises(ServiceError):
        await svc.get_latest_report(mock_pool, strategy_id="csm-set-01")


async def test_decode_report_unexpected_type_wrapped(mock_pool: MagicMock) -> None:
    """An unexpected column type (neither str nor dict) raises ``ServiceError``."""
    when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    mock_pool._conn.fetchrow.return_value = {
        "time": when,
        "report": 12345,  # neither str nor dict
        "computed_at": when,
    }

    with pytest.raises(ServiceError):
        await svc.get_latest_report(mock_pool, strategy_id="csm-set-01")


async def test_decode_report_invalid_payload_wrapped(mock_pool: MagicMock) -> None:
    """A JSON payload that fails model validation raises ``ServiceError``."""
    when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    mock_pool._conn.fetchrow.return_value = {
        "time": when,
        "report": json.dumps({"missing": "everything"}),
        "computed_at": when,
    }

    with pytest.raises(ServiceError):
        await svc.get_latest_report(mock_pool, strategy_id="csm-set-01")


# -----------------------------------------------------------------------------
# list_trades
# -----------------------------------------------------------------------------


def _trade_row(t: datetime, side: str = "LONG") -> dict[str, Any]:
    return {
        "time": t,
        "symbol": "PTT.BK",
        "side": side,
        "quantity": Decimal("100"),
        "entry_price": Decimal("34.50"),
        "exit_price": Decimal("35.00"),
        "realized_pnl": Decimal("50.00"),
        "duration_bars": 3,
        "commission": Decimal("2.00"),
    }


async def test_list_trades_returns_page(mock_csm_set_pool: MagicMock) -> None:
    """Happy path — returns a populated ``TradeLogPage``."""
    t1 = datetime(2026, 5, 19, 9, 30, tzinfo=UTC)
    t2 = datetime(2026, 5, 18, 9, 30, tzinfo=UTC)
    mock_csm_set_pool._conn.fetchval.return_value = 2
    mock_csm_set_pool._conn.fetch.return_value = [_trade_row(t1), _trade_row(t2, "SHORT")]

    page = await svc.list_trades(mock_csm_set_pool, strategy_id="csm-set-01", limit=10, offset=0)

    assert page.total == 2
    assert page.limit == 10
    assert page.offset == 0
    assert len(page.items) == 2
    assert page.items[0].side == "LONG"
    assert page.items[1].side == "SHORT"
    assert page.items[0].entry_time == t1


async def test_list_trades_applies_date_filters(mock_csm_set_pool: MagicMock) -> None:
    """The half-open upper bound is ``to_date + 1 day``."""
    mock_csm_set_pool._conn.fetchval.return_value = 0
    mock_csm_set_pool._conn.fetch.return_value = []

    await svc.list_trades(
        mock_csm_set_pool,
        strategy_id="csm-set-01",
        from_date=date(2026, 5, 1),
        to_date=date(2026, 5, 20),
        limit=50,
        offset=0,
    )

    count_args = mock_csm_set_pool._conn.fetchval.await_args.args
    assert count_args[2] == datetime(2026, 5, 1, tzinfo=UTC)
    assert count_args[3] == datetime(2026, 5, 21, tzinfo=UTC)


async def test_list_trades_treats_null_count_as_zero(mock_csm_set_pool: MagicMock) -> None:
    """fetchval returning ``None`` does not crash — total is 0."""
    mock_csm_set_pool._conn.fetchval.return_value = None
    mock_csm_set_pool._conn.fetch.return_value = []

    page = await svc.list_trades(mock_csm_set_pool, strategy_id="csm-set-01")
    assert page.total == 0


async def test_list_trades_rejects_bad_limit(mock_csm_set_pool: MagicMock) -> None:
    with pytest.raises(ServiceError):
        await svc.list_trades(mock_csm_set_pool, strategy_id="csm-set-01", limit=0)
    with pytest.raises(ServiceError):
        await svc.list_trades(mock_csm_set_pool, strategy_id="csm-set-01", limit=1001)


async def test_list_trades_rejects_negative_offset(mock_csm_set_pool: MagicMock) -> None:
    with pytest.raises(ServiceError):
        await svc.list_trades(mock_csm_set_pool, strategy_id="csm-set-01", offset=-1)


async def test_list_trades_db_error_wrapped(mock_csm_set_pool: MagicMock) -> None:
    mock_csm_set_pool._conn.fetchval.side_effect = asyncpg.PostgresError("bad")

    with pytest.raises(ServiceError):
        await svc.list_trades(mock_csm_set_pool, strategy_id="csm-set-01")


async def test_row_to_trade_handles_naive_time() -> None:
    """Direct exercise of the helper — naive ``time`` becomes UTC-aware."""
    naive = datetime(2026, 5, 19, 9, 30)
    entry = svc._row_to_trade(_trade_row(naive))
    assert entry.entry_time.tzinfo == UTC


# -----------------------------------------------------------------------------
# get_benchmark_curve
# -----------------------------------------------------------------------------


async def test_benchmark_curve_returns_raw(mock_csm_set_pool: MagicMock) -> None:
    """Happy path — raw values returned when ``normalize=False``."""
    t1 = datetime(2026, 5, 1, tzinfo=UTC)
    t2 = datetime(2026, 5, 2, tzinfo=UTC)
    mock_csm_set_pool._conn.fetch.return_value = [
        {"time": t1, "equity": Decimal("200000.0000")},
        {"time": t2, "equity": Decimal("203000.0000")},
    ]

    points = await svc.get_benchmark_curve(mock_csm_set_pool, strategy_id="csm-set-01")

    assert points == [
        BenchmarkPoint(date=t1, value=Decimal("200000.0000")),
        BenchmarkPoint(date=t2, value=Decimal("203000.0000")),
    ]


async def test_benchmark_curve_normalizes_to_base_100(
    mock_csm_set_pool: MagicMock,
) -> None:
    """When ``normalize=True``, every value is scaled to base 100."""
    t1 = datetime(2026, 5, 1, tzinfo=UTC)
    t2 = datetime(2026, 5, 2, tzinfo=UTC)
    mock_csm_set_pool._conn.fetch.return_value = [
        {"time": t1, "equity": Decimal("200")},
        {"time": t2, "equity": Decimal("210")},
    ]

    points = await svc.get_benchmark_curve(
        mock_csm_set_pool, strategy_id="csm-set-01", normalize=True
    )

    assert points[0].value == Decimal("100")
    assert points[1].value == Decimal("105")


async def test_benchmark_curve_zero_base_keeps_raw(mock_csm_set_pool: MagicMock) -> None:
    """A first equity of zero short-circuits normalisation (avoid divide-by-zero)."""
    t1 = datetime(2026, 5, 1, tzinfo=UTC)
    mock_csm_set_pool._conn.fetch.return_value = [
        {"time": t1, "equity": Decimal("0")},
        {"time": t1, "equity": Decimal("10")},
    ]

    points = await svc.get_benchmark_curve(
        mock_csm_set_pool, strategy_id="csm-set-01", normalize=True
    )
    assert points[0].value == Decimal("0")
    assert points[1].value == Decimal("10")


async def test_benchmark_curve_empty_short_circuits(
    mock_csm_set_pool: MagicMock,
) -> None:
    """No rows ⇒ empty list, even with ``normalize=True``."""
    mock_csm_set_pool._conn.fetch.return_value = []

    points = await svc.get_benchmark_curve(
        mock_csm_set_pool, strategy_id="csm-set-01", normalize=True
    )
    assert points == []


async def test_benchmark_curve_date_bounds(mock_csm_set_pool: MagicMock) -> None:
    """``to_date`` is converted to an exclusive ``+1 day`` upper bound."""
    mock_csm_set_pool._conn.fetch.return_value = []

    await svc.get_benchmark_curve(
        mock_csm_set_pool,
        strategy_id="csm-set-01",
        from_date=date(2026, 5, 1),
        to_date=date(2026, 5, 20),
    )
    args = mock_csm_set_pool._conn.fetch.await_args.args
    assert args[2] == datetime(2026, 5, 1, tzinfo=UTC)
    assert args[3] == datetime(2026, 5, 21, tzinfo=UTC)


async def test_benchmark_curve_db_error_wrapped(mock_csm_set_pool: MagicMock) -> None:
    mock_csm_set_pool._conn.fetch.side_effect = asyncpg.PostgresError("bad")

    with pytest.raises(ServiceError):
        await svc.get_benchmark_curve(mock_csm_set_pool, strategy_id="csm-set-01")


# -----------------------------------------------------------------------------
# StrategyReportNotFoundError message
# -----------------------------------------------------------------------------


def test_strategy_report_not_found_error_message() -> None:
    """The error message includes both id and (when present) date."""
    e1 = StrategyReportNotFoundError("csm-set-01")
    assert "csm-set-01" in str(e1)
    e2 = StrategyReportNotFoundError("csm-set-01", date="2026-05-20")
    assert "csm-set-01" in str(e2) and "2026-05-20" in str(e2)


# Suppress an unused-import warning when the module-level imports don't
# all exercise across every CI Python version.
_ = AsyncMock
