import json
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from src.schemas.gateway import (
    OverallPerformanceResponse,
    StrategyPerformanceResponse,
)


def _utc_now() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)


def _valid_strategy_response() -> StrategyPerformanceResponse:
    return StrategyPerformanceResponse(
        strategy_id="csm-set-01",
        daily_pnl=Decimal("15000.50"),
        total_value=Decimal("1050000.00"),
        max_drawdown=Decimal("-0.063"),
        sharpe_ratio=Decimal("1.85"),
        last_updated=_utc_now(),
    )


def _valid_overall_response() -> OverallPerformanceResponse:
    return OverallPerformanceResponse(
        total_portfolio_value=Decimal("1050000.00"),
        weighted_daily_return=Decimal("0.0148"),
        combined_max_drawdown=Decimal("-0.063"),
        active_strategies=1,
        allocation={"csm-set-01": Decimal("1.0")},
        strategies=[_valid_strategy_response()],
        computed_at=_utc_now(),
    )


# --- StrategyPerformanceResponse tests ---


def test_strategy_performance_response_construction() -> None:
    s = _valid_strategy_response()
    assert s.strategy_id == "csm-set-01"
    assert s.daily_pnl == Decimal("15000.50")
    assert s.last_updated.tzinfo == UTC


def test_strategy_performance_response_missing_required() -> None:
    with pytest.raises(ValidationError):
        StrategyPerformanceResponse(  # type: ignore[call-arg]
            strategy_id="csm-01",
            # missing required fields
        )


def test_strategy_performance_last_updated_naive_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        StrategyPerformanceResponse(
            strategy_id="csm-01",
            daily_pnl=Decimal("100.00"),
            total_value=Decimal("1000.00"),
            max_drawdown=Decimal("-0.05"),
            sharpe_ratio=Decimal("1.0"),
            last_updated=datetime(2026, 5, 14),  # naive
        )
    assert "timezone-aware" in str(exc_info.value)


def test_strategy_performance_last_updated_non_utc_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        StrategyPerformanceResponse(
            strategy_id="csm-01",
            daily_pnl=Decimal("100.00"),
            total_value=Decimal("1000.00"),
            max_drawdown=Decimal("-0.05"),
            sharpe_ratio=Decimal("1.0"),
            last_updated=datetime(2026, 5, 14, tzinfo=ZoneInfo("Asia/Tokyo")),
        )
    assert "UTC" in str(exc_info.value)


def test_strategy_performance_total_value_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        StrategyPerformanceResponse(
            strategy_id="csm-01",
            daily_pnl=Decimal("100.00"),
            total_value=Decimal("-1000.00"),
            max_drawdown=Decimal("-0.05"),
            sharpe_ratio=Decimal("1.0"),
            last_updated=_utc_now(),
        )


# --- OverallPerformanceResponse tests ---


def test_overall_performance_response_construction() -> None:
    r = _valid_overall_response()
    assert r.total_portfolio_value == Decimal("1050000.00")
    assert r.active_strategies == 1
    assert len(r.strategies) == 1


def test_empty_strategies_list_allowed() -> None:
    r = OverallPerformanceResponse(
        total_portfolio_value=Decimal("0.00"),
        weighted_daily_return=Decimal("0.0"),
        combined_max_drawdown=Decimal("0.0"),
        active_strategies=0,
        allocation={},
        strategies=[],
        computed_at=_utc_now(),
    )
    assert r.active_strategies == 0
    assert r.strategies == []


def test_active_strategies_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        OverallPerformanceResponse(
            total_portfolio_value=Decimal("0.00"),
            weighted_daily_return=Decimal("0.0"),
            combined_max_drawdown=Decimal("0.0"),
            active_strategies=-1,
            allocation={},
            strategies=[],
            computed_at=_utc_now(),
        )


def test_computed_at_naive_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        OverallPerformanceResponse(
            total_portfolio_value=Decimal("0.00"),
            weighted_daily_return=Decimal("0.0"),
            combined_max_drawdown=Decimal("0.0"),
            active_strategies=0,
            allocation={},
            strategies=[],
            computed_at=datetime(2026, 5, 14),  # naive
        )
    assert "timezone-aware" in str(exc_info.value)


# --- JSON serialization ---


def test_overall_performance_json_serialization() -> None:
    r = _valid_overall_response()
    data = r.model_dump(mode="json")
    assert isinstance(data, dict)
    assert "computed_at" in data


def test_datetime_fields_serialize_as_iso8601() -> None:
    r = _valid_overall_response()
    json_str = r.model_dump_json()
    data = json.loads(json_str)
    assert data["computed_at"] == "2026-05-14T12:00:00Z"


def test_decimal_fields_serialize_as_numbers() -> None:
    s = _valid_strategy_response()
    data = s.model_dump()
    assert isinstance(data["daily_pnl"], Decimal)
    assert data["daily_pnl"] == Decimal("15000.50")


def test_no_extra_fields_in_serialization() -> None:
    s = _valid_strategy_response()
    data = s.model_dump()
    expected_keys = {
        "strategy_id",
        "daily_pnl",
        "total_value",
        "max_drawdown",
        "sharpe_ratio",
        "last_updated",
    }
    assert set(data.keys()) == expected_keys


def test_allocation_decimal_values() -> None:
    r = OverallPerformanceResponse(
        total_portfolio_value=Decimal("2000000.00"),
        weighted_daily_return=Decimal("0.02"),
        combined_max_drawdown=Decimal("-0.04"),
        active_strategies=2,
        allocation={
            "csm-set-01": Decimal("0.6"),
            "tfex-01": Decimal("0.4"),
        },
        strategies=[_valid_strategy_response()],
        computed_at=_utc_now(),
    )
    assert r.allocation["csm-set-01"] == Decimal("0.6")
    assert r.allocation["tfex-01"] == Decimal("0.4")
    assert r.allocation["csm-set-01"] + r.allocation["tfex-01"] == Decimal("1.0")


# --- Whitespace stripping on StrategyPerformanceResponse ---


def test_strategy_response_strips_whitespace() -> None:
    s = StrategyPerformanceResponse(
        strategy_id="  csm-01  ",
        daily_pnl=Decimal("100.00"),
        total_value=Decimal("1000.00"),
        max_drawdown=Decimal("-0.05"),
        sharpe_ratio=Decimal("1.0"),
        last_updated=_utc_now(),
    )
    assert s.strategy_id == "csm-01"
