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


# --- extended_data.report parsing -------------------------------------------


def _report_dict() -> dict[str, object]:
    """Build a minimal-but-valid ``StrategyReport`` JSON-like dict."""
    side = {
        "initial_capital": "200000.00",
        "open_pnl": "0",
        "net_pnl": "100.00",
        "gross_profit": "200.00",
        "gross_loss": "-100.00",
        "profit_factor": "2.00",
        "commission_paid": "5.00",
        "expected_payoff": "10.00",
    }
    detail_side = {
        "total_trades": 5,
        "total_open_trades": 0,
        "winning_trades": 3,
        "losing_trades": 2,
        "percent_profitable": "0.6000",
        "avg_pnl": "20.00",
        "avg_winning_trade": "50.00",
        "avg_losing_trade": "-25.00",
        "ratio_avg_win_avg_loss": "2.00",
        "largest_winning_trade": "75.00",
        "largest_winning_trade_pct": "0.0375",
        "largest_winner_pct_of_gross_profit": "0.3750",
        "largest_losing_trade": "-40.00",
        "largest_losing_trade_pct": "-0.0200",
        "largest_loser_pct_of_gross_loss": "0.4000",
        "outliers_count": 0,
        "outliers_pnl": "0",
        "avg_bars_in_trades": "4.0",
        "avg_bars_in_winning_trades": "4.5",
        "avg_bars_in_losing_trades": "3.5",
    }
    capital_row = {
        "annualized_return_cagr": "0.1000",
        "return_on_initial_capital": "0.0500",
        "account_size_required": "210000",
        "return_on_account_size_required": "0.0476",
        "net_profit_pct_of_largest_loss": "2.5",
    }
    return {
        "as_of": "2026-05-14T12:00:00+00:00",
        "currency": "THB",
        "initial_capital": "200000.00",
        "headline": {
            "total_pnl": "100.00",
            "total_pnl_pct": "0.0005",
            "max_equity_drawdown": "-50.00",
            "max_equity_drawdown_pct": "-0.00025",
            "total_trades": 5,
            "profitable_trades": 3,
            "profitable_pct": "0.6000",
            "profit_factor": "2.00",
        },
        "profit_structure": {
            "total_profit": "200",
            "open_pnl": "0",
            "total_loss": "-100",
            "commission": "-5",
            "net_pnl": "95",
        },
        "returns": {"all": side, "long": side, "short": side},
        "risk_adjusted": {"sharpe_ratio": "1.50", "sortino_ratio": "2.10"},
        "trades_analysis": {
            "pnl_distribution": [],
            "win_loss_split": {"wins": 3, "losses": 2, "breakeven": 0},
            "avg_loss_pct": "-0.0125",
            "avg_profit_pct": "0.0167",
        },
        "details": {"all": detail_side, "long": detail_side, "short": detail_side},
        "capital_efficiency": {
            "capital_usage": {"all": capital_row, "long": capital_row, "short": capital_row},
            "margin_usage": {},
        },
        "runups_drawdowns": {
            "runups": {
                "avg_duration_days": "1.5",
                "avg_runup": "60.00",
                "max_runup_close_to_close": "150.00",
            },
            "drawdowns": {
                "avg_duration_days": "1.0",
                "avg_drawdown": "-30.00",
                "max_drawdown_close_to_close": "-50.00",
                "return_of_max_drawdown": "-0.00025",
            },
        },
        "trades": [],
        "benchmark_equity_curve": [],
    }


def test_payload_parses_extended_report() -> None:
    """When ``extended_data['report']`` is present and valid, it is parsed."""
    payload = StrategyPayload(
        strategy_metadata=_valid_metadata(),
        performance_metrics=_valid_metrics(),
        current_exposure=_valid_exposure(),
        extended_data={"report": _report_dict()},
    )

    assert payload.parsed_report is not None
    assert payload.parsed_report.headline.total_trades == 5


def test_payload_without_report_has_none_parsed_report() -> None:
    """Payloads with no ``report`` key parse cleanly with ``parsed_report = None``."""
    payload = _valid_payload()
    assert payload.parsed_report is None


def test_payload_with_invalid_report_logs_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An invalid ``report`` does not raise — payload is accepted, WARNING logged."""
    with caplog.at_level("WARNING", logger="src.schemas.strategy"):
        payload = StrategyPayload(
            strategy_metadata=_valid_metadata(),
            performance_metrics=_valid_metrics(),
            current_exposure=_valid_exposure(),
            extended_data={"report": {"totally": "wrong"}},
        )

    assert payload.parsed_report is None
    assert any("extended_data.report failed to parse" in rec.message for rec in caplog.records)
