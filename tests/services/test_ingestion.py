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


def test_payload_to_row_daily_return_formula() -> None:
    row = ingest_mod._payload_to_row(_payload(daily_pnl="15000.50", total_value="1050000.00"))
    expected = 15000.50 / 1050000.00
    assert row["daily_return"] == pytest.approx(expected, rel=1e-9)


def test_payload_to_row_total_value_zero_yields_zero_return() -> None:
    payload = _payload(daily_pnl="0.00", total_value="0.00")
    row = ingest_mod._payload_to_row(payload)
    assert row["daily_return"] == 0.0


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
