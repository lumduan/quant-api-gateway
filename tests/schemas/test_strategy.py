from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from src.schemas.strategy import (
    CurrentExposure,
    EquityPoint,
    PerformanceMetrics,
    StrategyMetadata,
    StrategyPayload,
)


def _utc_now() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)


def _valid_metadata() -> StrategyMetadata:
    return StrategyMetadata(
        id="csm-set-01",
        type="equity-long",
        last_updated=_utc_now(),
    )


def _valid_metrics() -> PerformanceMetrics:
    return PerformanceMetrics(
        daily_pnl=Decimal("15000.50"),
        equity_curve=[
            EquityPoint(date="2026-05-14", value=Decimal("1050000.00")),
        ],
        max_drawdown=Decimal("-0.063"),
        sharpe_ratio=Decimal("1.85"),
    )


def _valid_exposure() -> CurrentExposure:
    return CurrentExposure(
        total_value=Decimal("1050000.00"),
        cash_balance=Decimal("50000.00"),
        positions_count=5,
    )


def _valid_payload() -> StrategyPayload:
    return StrategyPayload(
        strategy_metadata=_valid_metadata(),
        performance_metrics=_valid_metrics(),
        current_exposure=_valid_exposure(),
    )


# --- StrategyPayload tests ---


def test_valid_strategy_payload() -> None:
    payload = _valid_payload()
    assert payload.strategy_metadata.id == "csm-set-01"
    assert payload.performance_metrics.daily_pnl == Decimal("15000.50")
    assert payload.current_exposure.positions_count == 5


def test_strategy_payload_missing_required_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        StrategyPayload(  # type: ignore[call-arg]
            strategy_metadata=_valid_metadata(),
            current_exposure=_valid_exposure(),
            # performance_metrics omitted
        )
    assert "performance_metrics" in str(exc_info.value)


def test_extended_data_defaults_to_empty_dict() -> None:
    payload = _valid_payload()
    assert payload.extended_data == {}


def test_extended_data_accepts_arbitrary_keys() -> None:
    payload = StrategyPayload(
        strategy_metadata=_valid_metadata(),
        performance_metrics=_valid_metrics(),
        current_exposure=_valid_exposure(),
        extended_data={"custom_metric": 42, "tags": ["volatile"]},
    )
    assert payload.extended_data["custom_metric"] == 42


# --- PerformanceMetrics tests ---


def test_empty_equity_curve_rejected() -> None:
    with pytest.raises(ValidationError):
        PerformanceMetrics(
            daily_pnl=Decimal("100.00"),
            equity_curve=[],
            max_drawdown=Decimal("-0.05"),
            sharpe_ratio=Decimal("1.0"),
        )


def test_max_drawdown_positive_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PerformanceMetrics(
            daily_pnl=Decimal("100.00"),
            equity_curve=[EquityPoint(date="2026-05-14", value=Decimal("1000.00"))],
            max_drawdown=Decimal("0.05"),
            sharpe_ratio=Decimal("1.0"),
        )
    assert "max_drawdown" in str(exc_info.value)


def test_max_drawdown_zero_accepted() -> None:
    metrics = PerformanceMetrics(
        daily_pnl=Decimal("100.00"),
        equity_curve=[EquityPoint(date="2026-05-14", value=Decimal("1000.00"))],
        max_drawdown=Decimal("0.00"),
        sharpe_ratio=Decimal("1.0"),
    )
    assert metrics.max_drawdown == Decimal("0.00")


# --- CurrentExposure tests ---


def test_total_value_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        CurrentExposure(
            total_value=Decimal("-100.00"),
            cash_balance=Decimal("50.00"),
            positions_count=1,
        )


def test_cash_balance_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        CurrentExposure(
            total_value=Decimal("100.00"),
            cash_balance=Decimal("-1.00"),
            positions_count=1,
        )


def test_positions_count_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        CurrentExposure(
            total_value=Decimal("100.00"),
            cash_balance=Decimal("50.00"),
            positions_count=-1,
        )


# --- EquityPoint tests ---


def test_equity_point_invalid_date_pattern() -> None:
    with pytest.raises(ValidationError):
        EquityPoint(date="01-01-2026", value=Decimal("1000.00"))


def test_equity_point_valid_date() -> None:
    point = EquityPoint(date="2026-01-01", value=Decimal("1000.00"))
    assert point.date == "2026-01-01"


# --- UTC datetime enforcement ---


def test_strategy_metadata_datetime_naive_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        StrategyMetadata(
            id="csm-01",
            type="equity",
            last_updated=datetime(2026, 5, 14),  # naive
        )
    assert "timezone-aware" in str(exc_info.value)


def test_strategy_metadata_datetime_non_utc_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        StrategyMetadata(
            id="csm-01",
            type="equity",
            last_updated=datetime(2026, 5, 14, tzinfo=ZoneInfo("America/New_York")),
        )
    assert "UTC" in str(exc_info.value)


def test_strategy_metadata_datetime_utc_accepted() -> None:
    meta = StrategyMetadata(
        id="csm-01",
        type="equity",
        last_updated=datetime(2026, 5, 14, tzinfo=UTC),
    )
    assert meta.last_updated.tzinfo == UTC


# --- Whitespace stripping ---


def test_strategy_metadata_strips_whitespace() -> None:
    meta = StrategyMetadata(
        id="  csm-01  ",
        type="equity",
        last_updated=_utc_now(),
    )
    assert meta.id == "csm-01"


def test_strategy_payload_strips_metadata_whitespace() -> None:
    payload = StrategyPayload(
        strategy_metadata=StrategyMetadata(
            id="  csm-01  ",
            type="  equity-long  ",
            last_updated=_utc_now(),
        ),
        performance_metrics=_valid_metrics(),
        current_exposure=_valid_exposure(),
    )
    assert payload.strategy_metadata.id == "csm-01"
    assert payload.strategy_metadata.type == "equity-long"


# --- Model immutability (frozen=True) ---


def test_strategy_payload_is_frozen() -> None:
    payload = _valid_payload()
    with pytest.raises(ValidationError):
        payload.strategy_metadata.id = "changed"


def test_equity_point_is_frozen() -> None:
    point = EquityPoint(date="2026-05-14", value=Decimal("1000.00"))
    with pytest.raises(ValidationError):
        point.value = Decimal("2000.00")
