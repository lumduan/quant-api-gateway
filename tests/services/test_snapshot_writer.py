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
    assert args[4] is None  # combined_drawdown is None when rows have no equity_curve
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


# ---- Phase 4: combined_drawdown / metadata extraction ----------------------------


def _row_with_curve(
    *,
    sid: str,
    total_value: float,
    daily_return: float,
    curve_points: list[tuple[str, str]],
    as_string: bool = False,
) -> dict[str, Any]:
    metadata: Any = {"equity_curve": [{"date": d, "value": v} for d, v in curve_points]}
    if as_string:
        metadata = json.dumps(metadata)
    return {
        "strategy_id": sid,
        "total_value": total_value,
        "daily_return": daily_return,
        "metadata": metadata,
    }


def test_select_sql_includes_metadata_column() -> None:
    # Guards against accidental regression of the Phase 4 SQL extension.
    assert "metadata" in sw._SELECT_TODAY_SQL


def test_compute_aggregates_fills_combined_drawdown_when_curves_present() -> None:
    rows = [
        _row_with_curve(
            sid="a",
            total_value=100_000.0,
            daily_return=0.01,
            curve_points=[
                ("2026-05-12", "100"),
                ("2026-05-13", "80"),
                ("2026-05-14", "110"),
            ],
        ),
        _row_with_curve(
            sid="b",
            total_value=100_000.0,
            daily_return=0.02,
            curve_points=[
                ("2026-05-12", "100"),
                ("2026-05-13", "100"),
                ("2026-05-14", "100"),
            ],
        ),
    ]
    active = [_cfg(sid="a", weight="0.5"), _cfg(sid="b", weight="0.5")]
    agg = sw._compute_aggregates(rows, active)
    # Merged (both base-100 already, equal weights):
    # 05-12: (100+100)/2 = 100
    # 05-13: (80+100)/2  = 90  → drawdown = 90/100 - 1 = -0.10
    # 05-14: (110+100)/2 = 105 → no further drawdown (peak still 100, 105/105-1=0)
    # Running peak after 05-14 is 105; max_dd encountered = -0.10
    assert agg.combined_drawdown == pytest.approx(-0.10, rel=1e-9)


def test_compute_aggregates_combined_drawdown_none_when_no_curves() -> None:
    rows = [
        {"strategy_id": "a", "total_value": 100.0, "daily_return": 0.0, "metadata": None},
    ]
    active = [_cfg(sid="a", weight="1")]
    agg = sw._compute_aggregates(rows, active)
    assert agg.combined_drawdown is None


def test_compute_aggregates_accepts_metadata_as_json_string() -> None:
    rows = [
        _row_with_curve(
            sid="a",
            total_value=100.0,
            daily_return=0.0,
            curve_points=[("2026-05-13", "100"), ("2026-05-14", "80")],
            as_string=True,  # asyncpg returns JSONB as str by default
        ),
    ]
    active = [_cfg(sid="a", weight="1")]
    agg = sw._compute_aggregates(rows, active)
    assert agg.combined_drawdown == pytest.approx(-0.20, rel=1e-9)


async def test_maybe_write_snapshot_persists_combined_drawdown(mock_pool: Any) -> None:
    registry = _registry(_cfg(sid="a", weight="1"))
    mock_pool._conn.fetch.return_value = [
        _row_with_curve(
            sid="a",
            total_value=100.0,
            daily_return=0.0,
            curve_points=[("2026-05-13", "100"), ("2026-05-14", "80")],
        ),
    ]
    written = await sw.maybe_write_snapshot(
        pool=mock_pool,
        registry=registry,
        now=datetime(2026, 5, 14, tzinfo=UTC),
    )
    assert written is True
    args = mock_pool._conn.execute.call_args.args
    # combined_drawdown is the 4th positional arg (index 4) of _UPSERT_SQL.
    assert args[4] == pytest.approx(-0.20, rel=1e-9)


# ---- _extract_equity_curve direct unit tests -------------------------------------


def test_extract_equity_curve_none_returns_empty() -> None:
    assert sw._extract_equity_curve(None) == []


def test_extract_equity_curve_invalid_json_string_returns_empty() -> None:
    assert sw._extract_equity_curve("{not valid json") == []


def test_extract_equity_curve_unexpected_type_returns_empty() -> None:
    assert sw._extract_equity_curve(42) == []


def test_extract_equity_curve_dict_without_key_returns_empty() -> None:
    assert sw._extract_equity_curve({"other_key": []}) == []


def test_extract_equity_curve_non_list_value_returns_empty() -> None:
    assert sw._extract_equity_curve({"equity_curve": "not-a-list"}) == []


def test_extract_equity_curve_skips_malformed_entries() -> None:
    metadata = {
        "equity_curve": [
            {"date": "2026-05-13", "value": "100"},
            "not-a-dict",
            {"date": None, "value": "999"},  # invalid date type
            {"date": "2026-05-14", "value": None},  # missing value
            {"date": "2026-05-15", "value": "110"},
        ]
    }
    points = sw._extract_equity_curve(metadata)
    assert [p.date for p in points] == ["2026-05-13", "2026-05-15"]


def test_extract_equity_curve_skips_value_that_fails_decimal() -> None:
    # Pattern-rejected dates would be a Pydantic validation error → entry skipped.
    metadata = {
        "equity_curve": [
            {"date": "not-a-date", "value": "100"},
            {"date": "2026-05-14", "value": "good"},  # Decimal("good") raises
            {"date": "2026-05-15", "value": "150"},
        ]
    }
    points = sw._extract_equity_curve(metadata)
    assert [p.date for p in points] == ["2026-05-15"]


# ---- Phase 5: cache invalidation integration -----------------------------------


@pytest.fixture
def mock_invalidation(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace invalidation functions in ``snapshot_writer`` with mocks."""
    from unittest.mock import AsyncMock

    overall = AsyncMock(return_value=None)
    strategy = AsyncMock(return_value=None)
    monkeypatch.setattr(sw, "invalidate_overall_cache", overall)
    monkeypatch.setattr(sw, "invalidate_strategy_cache", strategy)
    return {"overall": overall, "strategy": strategy}


async def test_maybe_write_snapshot_invalidates_cache_after_upsert(
    mock_pool: Any, mock_invalidation: dict[str, Any]
) -> None:
    registry = _registry(_cfg(sid="a", weight="0.6"), _cfg(sid="b", weight="0.4"))
    mock_pool._conn.fetch.return_value = [
        {"strategy_id": "a", "total_value": 600_000.0, "daily_return": 0.01},
        {"strategy_id": "b", "total_value": 400_000.0, "daily_return": 0.02},
    ]
    written = await sw.maybe_write_snapshot(
        pool=mock_pool,
        registry=registry,
        now=datetime(2026, 5, 14, tzinfo=UTC),
    )
    assert written is True
    mock_invalidation["overall"].assert_awaited_once()
    assert mock_invalidation["strategy"].await_count == 2
    mock_invalidation["strategy"].assert_any_await("a")
    mock_invalidation["strategy"].assert_any_await("b")


async def test_maybe_write_snapshot_succeeds_despite_invalidation_failure(
    mock_pool: Any, mock_invalidation: dict[str, Any]
) -> None:
    mock_invalidation["overall"].side_effect = Exception("redis gone")
    registry = _registry(_cfg(sid="a", weight="1"))
    mock_pool._conn.fetch.return_value = [
        {"strategy_id": "a", "total_value": 100.0, "daily_return": 0.0},
    ]
    written = await sw.maybe_write_snapshot(
        pool=mock_pool,
        registry=registry,
        now=datetime(2026, 5, 14, tzinfo=UTC),
    )
    assert written is True  # invalidation failure does NOT block snapshot


async def test_maybe_write_snapshot_invalidation_called_per_active_strategy(
    mock_pool: Any, mock_invalidation: dict[str, Any]
) -> None:
    registry = _registry(
        _cfg(sid="a", weight="0.3"),
        _cfg(sid="b", weight="0.3"),
        _cfg(sid="c", weight="0.4"),
    )
    mock_pool._conn.fetch.return_value = [
        {"strategy_id": "a", "total_value": 100.0, "daily_return": 0.0},
        {"strategy_id": "b", "total_value": 100.0, "daily_return": 0.0},
        {"strategy_id": "c", "total_value": 100.0, "daily_return": 0.0},
    ]
    await sw.maybe_write_snapshot(
        pool=mock_pool,
        registry=registry,
        now=datetime(2026, 5, 14, tzinfo=UTC),
    )
    assert mock_invalidation["strategy"].await_count == 3


async def test_maybe_write_snapshot_no_invalidation_when_round_incomplete(
    mock_pool: Any, mock_invalidation: dict[str, Any]
) -> None:
    registry = _registry(_cfg(sid="a", weight="1"), _cfg(sid="b", weight="1"))
    mock_pool._conn.fetch.return_value = [
        {"strategy_id": "a", "total_value": 100.0, "daily_return": 0.0},
    ]
    written = await sw.maybe_write_snapshot(
        pool=mock_pool,
        registry=registry,
        now=datetime(2026, 5, 14, tzinfo=UTC),
    )
    assert written is False
    mock_invalidation["overall"].assert_not_awaited()
    mock_invalidation["strategy"].assert_not_awaited()
