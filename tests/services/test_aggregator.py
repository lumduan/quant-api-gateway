"""Tests for ``src.services.aggregator``."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from src.schemas.gateway import StrategyPerformanceResponse
from src.schemas.strategy import EquityPoint
from src.services.aggregator import (
    calculate_combined_drawdown,
    calculate_weighted_return,
    merge_equity_curves,
)


def _strategy(
    *,
    sid: str,
    daily_pnl: str,
    total_value: str,
    max_drawdown: str = "-0.05",
    sharpe_ratio: str = "1.0",
) -> StrategyPerformanceResponse:
    return StrategyPerformanceResponse(
        strategy_id=sid,
        daily_pnl=Decimal(daily_pnl),
        total_value=Decimal(total_value),
        max_drawdown=Decimal(max_drawdown),
        sharpe_ratio=Decimal(sharpe_ratio),
        last_updated=datetime(2026, 5, 14, 11, 0, tzinfo=UTC),
    )


def _curve(values: list[tuple[str, str]]) -> list[EquityPoint]:
    return [EquityPoint(date=d, value=Decimal(v)) for d, v in values]


# ---- calculate_weighted_return ---------------------------------------------------


def test_weighted_return_two_strategies_60_40() -> None:
    strategies = [
        _strategy(sid="a", daily_pnl="1500", total_value="100000"),
        _strategy(sid="b", daily_pnl="-400", total_value="50000"),
    ]
    weights = {"a": 0.6, "b": 0.4}
    # (1500/100000)*0.6 + (-400/50000)*0.4 = 0.015*0.6 + (-0.008)*0.4 = 0.0058
    assert calculate_weighted_return(strategies, weights) == pytest.approx(0.0058, rel=1e-9)


def test_weighted_return_single_strategy() -> None:
    strategies = [_strategy(sid="a", daily_pnl="1000", total_value="100000")]
    assert calculate_weighted_return(strategies, {"a": 1.0}) == pytest.approx(0.01)


def test_weighted_return_all_weights_zero() -> None:
    strategies = [_strategy(sid="a", daily_pnl="1000", total_value="100000")]
    assert calculate_weighted_return(strategies, {"a": 0.0, "b": 0.0}) == 0.0


def test_weighted_return_total_value_zero_excluded() -> None:
    strategies = [
        _strategy(sid="a", daily_pnl="100", total_value="0"),
        _strategy(sid="b", daily_pnl="100", total_value="10000"),
    ]
    weights = {"a": 0.5, "b": 0.5}
    # 'a' is dropped; only 'b' contributes: (100/10000)*0.5 / 1.0 = 0.005
    assert calculate_weighted_return(strategies, weights) == pytest.approx(0.005)


def test_weighted_return_empty_strategies() -> None:
    assert calculate_weighted_return([], {"a": 1.0}) == 0.0


def test_weighted_return_empty_weights() -> None:
    strategies = [_strategy(sid="a", daily_pnl="100", total_value="10000")]
    assert calculate_weighted_return(strategies, {}) == 0.0


def test_weighted_return_missing_weight_for_strategy() -> None:
    strategies = [
        _strategy(sid="a", daily_pnl="100", total_value="10000"),
        _strategy(sid="b", daily_pnl="200", total_value="10000"),
    ]
    # 'b' is missing from weights → contributes 0.
    assert calculate_weighted_return(strategies, {"a": 1.0}) == pytest.approx(0.01)


# ---- merge_equity_curves ---------------------------------------------------------


def test_merge_equity_curves_aligned_dates() -> None:
    curves = {
        "a": _curve([("2026-05-13", "100"), ("2026-05-14", "110")]),
        "b": _curve([("2026-05-13", "200"), ("2026-05-14", "220")]),
    }
    merged = merge_equity_curves(curves, {"a": 0.5, "b": 0.5})
    assert [p.date for p in merged] == ["2026-05-13", "2026-05-14"]
    # Both normalise to 100/110, so the weighted sum is 100/110.
    assert merged[0].value == Decimal("100.0000")
    assert merged[1].value == Decimal("110.0000")


def test_merge_equity_curves_different_date_ranges_outer_join() -> None:
    curves = {
        "a": _curve(
            [
                ("2026-05-12", "100"),
                ("2026-05-13", "110"),
                ("2026-05-14", "120"),
            ]
        ),
        "b": _curve([("2026-05-13", "50"), ("2026-05-14", "55")]),
    }
    merged = merge_equity_curves(curves, {"a": 0.5, "b": 0.5})
    assert [p.date for p in merged] == ["2026-05-12", "2026-05-13", "2026-05-14"]
    # 'a' normalised: 100, 110, 120; 'b' normalised: 100, 110.
    # 2026-05-12: only 'a' → 100.0
    # 2026-05-13: (110*0.5 + 100*0.5) / 1.0 = 105.0
    # 2026-05-14: (120*0.5 + 110*0.5) / 1.0 = 115.0
    assert merged[0].value == Decimal("100.0000")
    assert merged[1].value == Decimal("105.0000")
    assert merged[2].value == Decimal("115.0000")


def test_merge_equity_curves_forward_fill_missing_dates() -> None:
    curves = {
        "a": _curve([("2026-05-12", "100"), ("2026-05-14", "110")]),  # missing 05-13
        "b": _curve(
            [
                ("2026-05-12", "100"),
                ("2026-05-13", "100"),
                ("2026-05-14", "100"),
            ]
        ),
    }
    merged = merge_equity_curves(curves, {"a": 0.5, "b": 0.5})
    # a normalised: d12=100, d14=110. After ffill, d13=100 (carried from d12).
    # b normalised: 100, 100, 100.
    # Weighted (sum/total_weight, both present each row):
    # d12: (100+100)/1 = 100
    # d13: (100+100)/1 = 100
    # d14: (110+100)/1 = 105
    assert [p.date for p in merged] == ["2026-05-12", "2026-05-13", "2026-05-14"]
    assert merged[0].value == Decimal("100.0000")
    assert merged[1].value == Decimal("100.0000")
    assert merged[2].value == Decimal("105.0000")


def test_merge_equity_curves_normalises_each_input_to_base_100() -> None:
    curves = {"a": _curve([("2026-05-13", "10000"), ("2026-05-14", "11000")])}
    merged = merge_equity_curves(curves, {"a": 1.0})
    # 10000 → 100, 11000 → 110 after normalisation.
    assert merged[0].value == Decimal("100.0000")
    assert merged[1].value == Decimal("110.0000")


def test_merge_equity_curves_empty_input() -> None:
    assert merge_equity_curves({}, {"a": 1.0}) == []


def test_merge_equity_curves_single_curve() -> None:
    curves = {"a": _curve([("2026-05-13", "50"), ("2026-05-14", "75")])}
    merged = merge_equity_curves(curves, {"a": 1.0})
    assert [p.date for p in merged] == ["2026-05-13", "2026-05-14"]
    assert merged[0].value == Decimal("100.0000")
    assert merged[1].value == Decimal("150.0000")


def test_merge_equity_curves_zero_weights() -> None:
    curves = {"a": _curve([("2026-05-13", "100")])}
    # weight of zero → strategy is dropped → empty output
    assert merge_equity_curves(curves, {"a": 0.0}) == []


def test_merge_equity_curves_drops_empty_curve() -> None:
    curves = {
        "a": _curve([("2026-05-13", "100"), ("2026-05-14", "110")]),
        "b": [],
    }
    merged = merge_equity_curves(curves, {"a": 0.5, "b": 0.5})
    # 'b' is dropped (empty); 'a' is the only contributor.
    assert merged[0].value == Decimal("100.0000")
    assert merged[1].value == Decimal("110.0000")


def test_merge_equity_curves_drops_curve_with_nonpositive_first_value() -> None:
    curves = {
        "a": _curve([("2026-05-13", "100"), ("2026-05-14", "110")]),
        "b": _curve([("2026-05-13", "0"), ("2026-05-14", "10")]),
    }
    merged = merge_equity_curves(curves, {"a": 1.0, "b": 1.0})
    # 'b' has first_value 0 → dropped. Result reflects only 'a'.
    assert merged[0].value == Decimal("100.0000")
    assert merged[1].value == Decimal("110.0000")


def test_merge_equity_curves_strategy_missing_from_weights_is_dropped() -> None:
    curves = {
        "a": _curve([("2026-05-13", "100"), ("2026-05-14", "110")]),
        "b": _curve([("2026-05-13", "100"), ("2026-05-14", "200")]),
    }
    # Only 'a' has a weight → only 'a' contributes.
    merged = merge_equity_curves(curves, {"a": 1.0})
    assert merged[0].value == Decimal("100.0000")
    assert merged[1].value == Decimal("110.0000")


# ---- calculate_combined_drawdown -------------------------------------------------


def test_combined_drawdown_hand_built_curve() -> None:
    curves = {
        "a": _curve(
            [
                ("2026-05-10", "100"),
                ("2026-05-11", "110"),
                ("2026-05-12", "95"),
                ("2026-05-13", "120"),
                ("2026-05-14", "90"),
            ]
        ),
    }
    # Normalised: [100, 110, 95, 120, 90].
    # Running peaks: [100, 110, 110, 120, 120].
    # Drawdowns:    [0, 0, -0.1363…, 0, -0.25].
    # Max DD = -0.25.
    assert calculate_combined_drawdown(curves, {"a": 1.0}) == pytest.approx(-0.25, rel=1e-9)


def test_combined_drawdown_monotonic_increasing_returns_zero() -> None:
    curves = {
        "a": _curve([("2026-05-12", "100"), ("2026-05-13", "110"), ("2026-05-14", "120")]),
    }
    assert calculate_combined_drawdown(curves, {"a": 1.0}) == 0.0


def test_combined_drawdown_flat_curve_returns_zero() -> None:
    curves = {
        "a": _curve(
            [
                ("2026-05-12", "100"),
                ("2026-05-13", "100"),
                ("2026-05-14", "100"),
            ]
        ),
    }
    assert calculate_combined_drawdown(curves, {"a": 1.0}) == 0.0


def test_combined_drawdown_empty_curves_returns_zero() -> None:
    assert calculate_combined_drawdown({}, {"a": 1.0}) == 0.0


def test_combined_drawdown_skips_strategy_with_empty_curve() -> None:
    curves = {
        "a": _curve([("2026-05-13", "100"), ("2026-05-14", "80")]),
        "b": [],
    }
    # 'b' is dropped; 'a' alone gives drawdown 80/100 - 1 = -0.20.
    assert calculate_combined_drawdown(curves, {"a": 1.0, "b": 1.0}) == pytest.approx(-0.20)


def test_combined_drawdown_two_strategies_known_merged_drawdown() -> None:
    curves = {
        "a": _curve(
            [
                ("2026-05-12", "100"),
                ("2026-05-13", "50"),
                ("2026-05-14", "90"),
            ]
        ),
        "b": _curve(
            [
                ("2026-05-12", "100"),
                ("2026-05-13", "100"),
                ("2026-05-14", "100"),
            ]
        ),
    }
    # Normalised same as input (first=100 for both).
    # Per-row weighted (0.5/0.5):
    # 2026-05-12: 100
    # 2026-05-13: (50+100)/2 = 75
    # 2026-05-14: (90+100)/2 = 95
    # Running peaks: 100, 100, 100.
    # Drawdowns: 0, -0.25, -0.05.
    # Max DD = -0.25.
    weights = {"a": 0.5, "b": 0.5}
    assert calculate_combined_drawdown(curves, weights) == pytest.approx(-0.25, rel=1e-9)
