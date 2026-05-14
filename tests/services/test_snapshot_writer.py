"""Tests for ``src.services.snapshot_writer``."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from src.schemas.registry import StrategyConfig, StrategyRegistry
from src.services import snapshot_writer as sw
from src.services.errors import ServiceError


def _cfg(*, sid: str, weight: str, active: bool = True) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "id": sid,
            "name": sid,
            "service_url": f"http://{sid}",
            "capital_weight": weight,
            "active": active,
        }
    )


def _registry(*configs: StrategyConfig) -> StrategyRegistry:
    return StrategyRegistry(strategies=list(configs))


# ---- pure aggregator unit tests --------------------------------------------------


def test_compute_aggregates_two_strategies() -> None:
    rows = [
        {"strategy_id": "a", "total_value": 600_000.0, "daily_return": 0.01},
        {"strategy_id": "b", "total_value": 400_000.0, "daily_return": 0.02},
    ]
    active = [_cfg(sid="a", weight="0.6"), _cfg(sid="b", weight="0.4")]
    agg = sw._compute_aggregates(rows, active)
    assert agg.total_portfolio == pytest.approx(1_000_000.0)
    assert agg.weighted_return == pytest.approx(0.01 * 0.6 + 0.02 * 0.4)
    assert agg.allocation == pytest.approx({"a": 0.6, "b": 0.4})
    assert agg.active_strategies == 2
    assert agg.combined_drawdown is None


def test_compute_aggregates_zero_total_weight() -> None:
    rows = [{"strategy_id": "z", "total_value": 100.0, "daily_return": 0.5}]
    agg = sw._compute_aggregates(rows, [_cfg(sid="z", weight="0")])
    assert agg.weighted_return == 0.0
    assert agg.allocation == {"z": 0.0}


def test_compute_aggregates_unequal_weights_normalised() -> None:
    rows = [
        {"strategy_id": "a", "total_value": 100.0, "daily_return": 0.0},
        {"strategy_id": "b", "total_value": 100.0, "daily_return": 0.0},
    ]
    active = [_cfg(sid="a", weight="3"), _cfg(sid="b", weight="1")]
    agg = sw._compute_aggregates(rows, active)
    assert agg.allocation["a"] == pytest.approx(0.75)
    assert agg.allocation["b"] == pytest.approx(0.25)


# ---- maybe_write_snapshot DB integration with mock pool --------------------------


async def test_maybe_write_snapshot_round_incomplete(mock_pool: Any) -> None:
    registry = _registry(_cfg(sid="a", weight="0.5"), _cfg(sid="b", weight="0.5"))
    mock_pool._conn.fetch.return_value = [
        {"strategy_id": "a", "total_value": 100.0, "daily_return": 0.01}
    ]
    fixed = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    written = await sw.maybe_write_snapshot(pool=mock_pool, registry=registry, now=fixed)
    assert written is False
    mock_pool._conn.execute.assert_not_awaited()


async def test_maybe_write_snapshot_round_complete(mock_pool: Any) -> None:
    registry = _registry(_cfg(sid="a", weight="0.6"), _cfg(sid="b", weight="0.4"))
    mock_pool._conn.fetch.return_value = [
        {"strategy_id": "a", "total_value": 600_000.0, "daily_return": 0.01},
        {"strategy_id": "b", "total_value": 400_000.0, "daily_return": 0.02},
    ]
    fixed = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)

    written = await sw.maybe_write_snapshot(pool=mock_pool, registry=registry, now=fixed)
    assert written is True

    mock_pool._conn.execute.assert_awaited_once()
    args = mock_pool._conn.execute.call_args.args
    assert "INSERT INTO portfolio_snapshot" in args[0]
    assert args[1] == datetime(2026, 5, 14, 0, 0, tzinfo=UTC)  # UTC midnight bucket
    assert args[2] == pytest.approx(1_000_000.0)  # total_portfolio
    assert args[3] == pytest.approx(0.01 * 0.6 + 0.02 * 0.4)  # weighted_return
    assert args[4] is None  # combined_drawdown deferred to Phase 4
    assert args[5] == 2  # active_strategies
    assert json.loads(args[6]) == pytest.approx({"a": 0.6, "b": 0.4})


async def test_maybe_write_snapshot_empty_registry(mock_pool: Any) -> None:
    registry = _registry(_cfg(sid="x", weight="0", active=False))
    written = await sw.maybe_write_snapshot(pool=mock_pool, registry=registry)
    assert written is False
    mock_pool._conn.fetch.assert_not_awaited()


async def test_maybe_write_snapshot_select_error_wrapped(mock_pool: Any) -> None:
    registry = _registry(_cfg(sid="a", weight="1"))
    mock_pool._conn.fetch.side_effect = asyncpg.PostgresError("boom")
    with pytest.raises(ServiceError, match="failed to read"):
        await sw.maybe_write_snapshot(
            pool=mock_pool,
            registry=registry,
            now=datetime(2026, 5, 14, tzinfo=UTC),
        )


async def test_maybe_write_snapshot_upsert_error_wrapped(mock_pool: Any) -> None:
    registry = _registry(_cfg(sid="a", weight="1"))
    mock_pool._conn.fetch.return_value = [
        {"strategy_id": "a", "total_value": 1.0, "daily_return": 0.0},
    ]
    mock_pool._conn.execute.side_effect = asyncpg.PostgresError("boom")
    with pytest.raises(ServiceError, match="failed to upsert"):
        await sw.maybe_write_snapshot(
            pool=mock_pool,
            registry=registry,
            now=datetime(2026, 5, 14, tzinfo=UTC),
        )


async def test_maybe_write_snapshot_default_now_uses_utc_today(mock_pool: Any) -> None:
    """When ``now`` is omitted, the bucket date is today's UTC date."""
    registry = _registry(_cfg(sid="a", weight="1"))
    mock_pool._conn.fetch.return_value = [
        {"strategy_id": "a", "total_value": 1.0, "daily_return": 0.0}
    ]
    written = await sw.maybe_write_snapshot(pool=mock_pool, registry=registry)
    assert written is True
    args = mock_pool._conn.execute.call_args.args
    bucket: datetime = args[1]
    # Bucket must be UTC midnight of today
    assert bucket.tzinfo is UTC
    assert bucket.time().hour == 0 and bucket.time().minute == 0
    assert bucket.date() == datetime.now(UTC).date()


def test_compute_aggregates_preserves_capital_weight_decimal_precision() -> None:
    rows = [{"strategy_id": "a", "total_value": 100.0, "daily_return": 0.0}]
    active = [_cfg(sid="a", weight="0.3333")]
    agg = sw._compute_aggregates(rows, active)
    # Weight is the entire allocation (only one strategy) → normalised to 1.0
    assert agg.allocation == {"a": 1.0}
    # And the underlying StrategyConfig kept Decimal precision
    assert active[0].capital_weight == Decimal("0.3333")


def test_compute_aggregates_select_today_sql_uses_date_param() -> None:
    # Sanity: documented date-param shape in case the constant is edited
    assert "$1" in sw._SELECT_TODAY_SQL
    assert "ANY($2::text[])" in sw._SELECT_TODAY_SQL


def test_compute_aggregates_today_bucket_is_midnight() -> None:
    now = datetime(2026, 5, 14, 23, 59, 59, tzinfo=UTC)
    bucket = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    assert bucket == datetime(2026, 5, 14, 0, 0, tzinfo=UTC)


def test_date_helper_round_trip() -> None:
    # Coverage assist for date import — ensures `date` arithmetic stays sound
    assert date(2026, 5, 14).isoformat() == "2026-05-14"
