"""Tests for ``src.services.ingestion``."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from src.schemas.strategy import (
    CurrentExposure,
    EquityPoint,
    PerformanceMetrics,
    StrategyMetadata,
    StrategyPayload,
)
from src.services import ingestion as ingest_mod
from src.services.errors import IngestionPersistError


def _payload(
    *,
    strategy_id: str = "csm-set-01",
    daily_pnl: str = "15000.50",
    total_value: str = "1050000.00",
    cash_balance: str = "50000.00",
    equity_curve: list[tuple[str, str]] | None = None,
    max_drawdown: str = "-0.063",
    sharpe_ratio: str = "1.85",
    extended_data: dict[str, Any] | None = None,
) -> StrategyPayload:
    if equity_curve is None:
        equity_curve = [("2026-05-13", "1035000.00"), ("2026-05-14", "1050000.00")]
    return StrategyPayload(
        strategy_metadata=StrategyMetadata(
            id=strategy_id,
            type="equity-long",
            last_updated=datetime(2026, 5, 14, 11, 0, tzinfo=UTC),
        ),
        performance_metrics=PerformanceMetrics(
            daily_pnl=Decimal(daily_pnl),
            equity_curve=[EquityPoint(date=d, value=Decimal(v)) for d, v in equity_curve],
            max_drawdown=Decimal(max_drawdown),
            sharpe_ratio=Decimal(sharpe_ratio),
        ),
        current_exposure=CurrentExposure(
            total_value=Decimal(total_value),
            cash_balance=Decimal(cash_balance),
            positions_count=5,
        ),
        extended_data=extended_data or {},
    )


def test_payload_to_row_basic_fields() -> None:
    row = ingest_mod._payload_to_row(_payload())
    assert row["strategy_id"] == "csm-set-01"
    assert row["time"] == datetime(2026, 5, 14, 11, 0, tzinfo=UTC)
    assert row["total_value"] == pytest.approx(1050000.00)
    assert row["cash_balance"] == pytest.approx(50000.00)
    assert row["max_drawdown"] == pytest.approx(-0.063)
    assert row["sharpe_ratio"] == pytest.approx(1.85)


def test_payload_to_row_daily_return_divides_by_the_PRIOR_value() -> None:
    """A return is measured against what the period STARTED with.

    The default fixture curve is [1,035,000 -> 1,050,000], so the prior value is
    1,035,000 and `total_value` (today) is 1,050,000. The two denominators give
    visibly different answers, which is the whole point of the fix.
    """
    row = ingest_mod._payload_to_row(_payload(daily_pnl="15000.50", total_value="1050000.00"))
    assert row["daily_return"] == pytest.approx(15000.50 / 1035000.00, rel=1e-12)


def test_payload_to_row_daily_return_is_NOT_the_legacy_today_denominator() -> None:
    """Positive control for the bug itself: the old value must be unreachable.

    Phase 3 stored `daily_pnl / total_value`. On csm-set 2026-09-01 that put
    -0.03426456 in the column where the strategy's own engine said -0.03312940.
    """
    row = ingest_mod._payload_to_row(_payload(daily_pnl="15000.50", total_value="1050000.00"))
    legacy = 15000.50 / 1050000.00
    assert row["daily_return"] != pytest.approx(legacy, rel=1e-9)


def test_daily_return_understates_gains_and_overstates_losses() -> None:
    """The legacy bias is one-directional, which is why it never looked like noise."""
    gain = ingest_mod._daily_return(
        daily_pnl=15000.0,
        total_value=1015000.0,
        equity_curve=[EquityPoint(date="2026-05-13", value=Decimal("1000000"))] * 2,
        strategy_id="s",
    )
    assert gain > 15000.0 / 1015000.0, "legacy understates a gain"
    loss = ingest_mod._daily_return(
        daily_pnl=-15000.0,
        total_value=985000.0,
        equity_curve=[EquityPoint(date="2026-05-13", value=Decimal("1000000"))] * 2,
        strategy_id="s",
    )
    assert loss > -15000.0 / 985000.0, "legacy overstates a loss"


def test_payload_to_row_total_value_zero_yields_zero_return() -> None:
    payload = _payload(daily_pnl="0.00", total_value="0.00", equity_curve=[("2026-05-14", "0.00")])
    row = ingest_mod._payload_to_row(payload)
    assert row["daily_return"] == 0.0


def test_daily_return_falls_back_to_the_legacy_basis_when_no_prior_point() -> None:
    """A one-point curve carries no yesterday; behaviour is unchanged, not silently zeroed."""
    row = ingest_mod._payload_to_row(
        _payload(
            daily_pnl="15000.50",
            total_value="1050000.00",
            equity_curve=[("2026-05-14", "1050000.00")],
        )
    )
    assert row["daily_return"] == pytest.approx(15000.50 / 1050000.00, rel=1e-9)


def test_daily_return_fallback_WARNS_and_names_the_strategy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The degraded value is not the same quantity, so it must not land silently."""
    with caplog.at_level("WARNING"):
        ingest_mod._payload_to_row(
            _payload(strategy_id="lonely-curve", equity_curve=[("2026-05-14", "1050000.00")])
        )
    assert "lonely-curve" in caplog.text
    assert "LEGACY basis" in caplog.text


def test_daily_return_ignores_a_non_positive_prior_value() -> None:
    """A zero prior value would divide by zero; fall back rather than raise."""
    assert ingest_mod._daily_return(
        daily_pnl=100.0,
        total_value=1000.0,
        equity_curve=[
            EquityPoint(date="2026-05-13", value=Decimal("0")),
            EquityPoint(date="2026-05-14", value=Decimal("1000")),
        ],
        strategy_id="s",
    ) == pytest.approx(0.1)


def test_daily_return_reproduces_the_csm_set_2026_09_01_session() -> None:
    """The real numbers that exposed this, end to end."""
    assert ingest_mod._daily_return(
        daily_pnl=-43649.0,
        total_value=1273881.7,
        equity_curve=[
            EquityPoint(date="2026-08-31", value=Decimal("1317530.70")),
            EquityPoint(date="2026-09-01", value=Decimal("1273881.70")),
        ],
        strategy_id="csm-set",
    ) == pytest.approx(-0.03312939880641871, rel=1e-12)


def test_payload_to_row_cumulative_return_two_points() -> None:
    row = ingest_mod._payload_to_row(
        _payload(equity_curve=[("2026-05-13", "100.00"), ("2026-05-14", "110.00")])
    )
    assert row["cumulative_return"] == pytest.approx(0.10, rel=1e-9)


def test_payload_to_row_cumulative_return_single_point() -> None:
    row = ingest_mod._payload_to_row(_payload(equity_curve=[("2026-05-14", "1050000.00")]))
    assert row["cumulative_return"] is None


def test_payload_to_row_metadata_round_trip() -> None:
    payload = _payload(
        equity_curve=[("2026-05-13", "100.00"), ("2026-05-14", "110.00")],
        extended_data={"note": "kept", "n": 7},
    )
    row = ingest_mod._payload_to_row(payload)
    blob = json.loads(row["metadata_json"])
    assert blob["type"] == "equity-long"
    assert blob["positions_count"] == 5
    assert blob["daily_pnl"] == "15000.50"
    assert blob["equity_curve"] == [
        {"date": "2026-05-13", "value": "100.00"},
        {"date": "2026-05-14", "value": "110.00"},
    ]
    assert blob["extended_data"] == {"note": "kept", "n": 7}


def test_decimal_to_str_helper_rejects_non_decimal() -> None:
    with pytest.raises(TypeError, match="not JSON serializable"):
        ingest_mod._decimal_to_str(object())


async def test_persist_daily_report_executes_upsert(mock_pool: Any) -> None:
    payload = _payload()
    await ingest_mod.persist_daily_report(payload, pool=mock_pool)

    conn = mock_pool._conn
    conn.execute.assert_awaited_once()
    args = conn.execute.call_args.args
    assert "INSERT INTO daily_performance" in args[0]
    assert "ON CONFLICT (time, strategy_id)" in args[0]
    assert args[2] == "csm-set-01"  # strategy_id
    assert args[1] == datetime(2026, 5, 14, 11, 0, tzinfo=UTC)  # time


async def test_persist_daily_report_wraps_postgres_error(mock_pool: Any) -> None:
    mock_pool._conn.execute.side_effect = asyncpg.PostgresError("boom")
    with pytest.raises(IngestionPersistError, match="failed to persist"):
        await ingest_mod.persist_daily_report(_payload(), pool=mock_pool)


async def test_persist_daily_report_with_report_executes_both_upserts(
    mock_pool: Any,
) -> None:
    """When the payload carries a parsed report, both UPSERTs run inside a tx."""
    from tests.schemas.test_strategy import _report_dict

    payload = _payload(extended_data={"report": _report_dict()})
    assert payload.parsed_report is not None

    await ingest_mod.persist_daily_report(payload, pool=mock_pool)

    conn = mock_pool._conn
    # One execute for daily_performance UPSERT, one for strategy_report_snapshot.
    assert conn.execute.await_count == 2
    sqls = [call.args[0] for call in conn.execute.await_args_list]
    assert any("INSERT INTO daily_performance" in s for s in sqls)
    assert any("strategy_report_snapshot" in s for s in sqls)
    # Transaction wrapper was used.
    conn.transaction.assert_called_once()


async def test_persist_daily_report_report_failure_wrapped(mock_pool: Any) -> None:
    """A failure on the report UPSERT surfaces as ``IngestionPersistError``."""
    from tests.schemas.test_strategy import _report_dict

    payload = _payload(extended_data={"report": _report_dict()})

    # First execute (daily_performance) succeeds; second (report) fails.
    conn = mock_pool._conn
    conn.execute.side_effect = [None, asyncpg.PostgresError("report write failed")]

    with pytest.raises(IngestionPersistError):
        await ingest_mod.persist_daily_report(payload, pool=mock_pool)
