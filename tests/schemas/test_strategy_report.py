"""Tests for :mod:`src.schemas.strategy_report`."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from src.schemas.strategy_report import (
    BenchmarkComparison,
    BenchmarkPoint,
    CapitalEfficiency,
    CapitalUsageRow,
    Details,
    DetailsRow,
    DrawdownRow,
    Headline,
    MarginUsage,
    PnLDistributionBucket,
    ProfitStructure,
    Returns,
    ReturnsRow,
    RiskAdjusted,
    RunUpRow,
    RunUpsDrawdowns,
    StrategyReport,
    TradeLogEntry,
    TradesAnalysis,
    WinLossSplit,
)


def _returns_row(initial: str = "200000.0000") -> ReturnsRow:
    return ReturnsRow(
        initial_capital=Decimal(initial),
        open_pnl=Decimal("0"),
        net_pnl=Decimal("3500.5000"),
        gross_profit=Decimal("8000"),
        gross_loss=Decimal("-4499.5"),
        profit_factor=Decimal("1.7780"),
        commission_paid=Decimal("125.00"),
        expected_payoff=Decimal("76.10"),
    )


def _details_row() -> DetailsRow:
    return DetailsRow(
        total_trades=46,
        total_open_trades=0,
        winning_trades=17,
        losing_trades=29,
        percent_profitable=Decimal("0.3695"),
        avg_pnl=Decimal("76.10"),
        avg_winning_trade=Decimal("470.59"),
        avg_losing_trade=Decimal("-155.16"),
        ratio_avg_win_avg_loss=Decimal("3.0330"),
        largest_winning_trade=Decimal("1500.00"),
        largest_winning_trade_pct=Decimal("0.0750"),
        largest_winner_pct_of_gross_profit=Decimal("0.1875"),
        largest_losing_trade=Decimal("-800.00"),
        largest_losing_trade_pct=Decimal("-0.0400"),
        largest_loser_pct_of_gross_loss=Decimal("0.1778"),
        outliers_count=2,
        outliers_pnl=Decimal("400.00"),
        avg_bars_in_trades=Decimal("5.2"),
        avg_bars_in_winning_trades=Decimal("6.1"),
        avg_bars_in_losing_trades=Decimal("4.8"),
    )


def _capital_usage_row() -> CapitalUsageRow:
    return CapitalUsageRow(
        annualized_return_cagr=Decimal("0.1250"),
        return_on_initial_capital=Decimal("0.0175"),
        account_size_required=Decimal("210000"),
        return_on_account_size_required=Decimal("0.0167"),
        net_profit_pct_of_largest_loss=Decimal("4.3750"),
    )


def _trade(entry: datetime, exit_: datetime) -> TradeLogEntry:
    return TradeLogEntry(
        entry_time=entry,
        exit_time=exit_,
        symbol="PTT.BK",
        side="LONG",
        qty=Decimal("100"),
        entry_price=Decimal("34.50"),
        exit_price=Decimal("35.25"),
        realized_pnl=Decimal("75.00"),
        duration_bars=5,
        commission=Decimal("3.50"),
    )


def _full_report() -> StrategyReport:
    as_of = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    return StrategyReport(
        as_of=as_of,
        currency="THB",
        initial_capital=Decimal("200000.0000"),
        headline=Headline(
            total_pnl=Decimal("3500.5000"),
            total_pnl_pct=Decimal("0.0175"),
            max_equity_drawdown=Decimal("-2100.00"),
            max_equity_drawdown_pct=Decimal("-0.0105"),
            total_trades=46,
            profitable_trades=17,
            profitable_pct=Decimal("0.3695"),
            profit_factor=Decimal("1.7780"),
        ),
        profit_structure=ProfitStructure(
            total_profit=Decimal("8000"),
            open_pnl=Decimal("0"),
            total_loss=Decimal("-4499.50"),
            commission=Decimal("-125.00"),
            net_pnl=Decimal("3500.50"),
        ),
        returns=Returns(all=_returns_row(), long=_returns_row(), short=_returns_row("0")),
        benchmark_comparison=BenchmarkComparison(
            buy_and_hold_return=Decimal("2900.00"),
            buy_and_hold_pct=Decimal("0.0145"),
            strategy_outperformance=Decimal("0.0030"),
        ),
        risk_adjusted=RiskAdjusted(
            sharpe_ratio=Decimal("1.420"),
            sortino_ratio=Decimal("2.155"),
        ),
        trades_analysis=TradesAnalysis(
            pnl_distribution=[
                PnLDistributionBucket(
                    bucket_low_pct=Decimal("-0.03"),
                    bucket_high_pct=Decimal("-0.02"),
                    count=1,
                    kind="loss",
                ),
            ],
            win_loss_split=WinLossSplit(wins=17, losses=29, breakeven=0),
            avg_loss_pct=Decimal("-0.0155"),
            avg_profit_pct=Decimal("0.0235"),
        ),
        details=Details(all=_details_row(), long=_details_row(), short=_details_row()),
        capital_efficiency=CapitalEfficiency(
            capital_usage={
                "all": _capital_usage_row(),
                "long": _capital_usage_row(),
                "short": _capital_usage_row(),
            },
            margin_usage=MarginUsage(),
        ),
        runups_drawdowns=RunUpsDrawdowns(
            runups=RunUpRow(
                avg_duration_days=Decimal("3.5"),
                avg_runup=Decimal("600.00"),
                max_runup_close_to_close=Decimal("1500.00"),
            ),
            drawdowns=DrawdownRow(
                avg_duration_days=Decimal("2.1"),
                avg_drawdown=Decimal("-300.00"),
                max_drawdown_close_to_close=Decimal("-2100.00"),
                return_of_max_drawdown=Decimal("-0.0105"),
            ),
        ),
        trades=[
            _trade(
                datetime(2026, 5, 19, 9, 30, tzinfo=UTC),
                datetime(2026, 5, 19, 15, 30, tzinfo=UTC),
            )
        ],
        benchmark_equity_curve=[
            BenchmarkPoint(date=as_of, value=Decimal("203450.0000")),
        ],
    )


def test_strategy_report_full_roundtrip() -> None:
    """A populated ``StrategyReport`` round-trips through ``model_dump_json``."""
    report = _full_report()
    raw = report.model_dump_json()
    parsed = StrategyReport.model_validate_json(raw)

    assert parsed == report
    # Decimals survive the round-trip as strings → Decimals.
    payload = json.loads(raw)
    assert payload["initial_capital"] == "200000.0000"
    assert payload["headline"]["profit_factor"] == "1.7780"


def test_strategy_report_rejects_naive_as_of() -> None:
    """``as_of`` must be tz-aware UTC."""
    report = _full_report()
    payload: dict[str, Any] = json.loads(report.model_dump_json())
    payload["as_of"] = "2026-05-20T11:00:00"  # naive

    with pytest.raises(ValidationError) as excinfo:
        StrategyReport.model_validate(payload)

    assert "as_of" in str(excinfo.value).lower()


def test_strategy_report_rejects_non_utc_as_of() -> None:
    """``as_of`` with a non-UTC tzinfo is rejected."""
    bangkok = timezone(timedelta(hours=7))
    payload = json.loads(_full_report().model_dump_json())
    payload["as_of"] = datetime(2026, 5, 20, 18, 0, tzinfo=bangkok).isoformat()

    with pytest.raises(ValidationError):
        StrategyReport.model_validate(payload)


def test_trade_log_entry_rejects_naive_entry_time() -> None:
    """``TradeLogEntry.entry_time`` must be tz-aware UTC."""
    payload = {
        "entry_time": "2026-05-19T09:30:00",
        "exit_time": "2026-05-19T15:30:00+00:00",
        "symbol": "PTT.BK",
        "side": "LONG",
        "qty": "100",
        "entry_price": "34.50",
        "exit_price": "35.25",
        "realized_pnl": "75.00",
        "duration_bars": 5,
        "commission": "3.50",
    }
    with pytest.raises(ValidationError):
        TradeLogEntry.model_validate(payload)


def test_trade_log_entry_rejects_naive_exit_time() -> None:
    """``TradeLogEntry.exit_time`` must be tz-aware UTC."""
    payload = {
        "entry_time": "2026-05-19T09:30:00+00:00",
        "exit_time": "2026-05-19T15:30:00",
        "symbol": "PTT.BK",
        "side": "LONG",
        "qty": "100",
        "entry_price": "34.50",
        "exit_price": "35.25",
        "realized_pnl": "75.00",
        "duration_bars": 5,
        "commission": "3.50",
    }
    with pytest.raises(ValidationError):
        TradeLogEntry.model_validate(payload)


def test_benchmark_point_rejects_naive_date() -> None:
    """``BenchmarkPoint.date`` must be tz-aware UTC."""
    with pytest.raises(ValidationError):
        BenchmarkPoint.model_validate({"date": "2026-05-20T11:00:00", "value": "203450.0000"})


def test_benchmark_point_accepts_utc_date() -> None:
    """``BenchmarkPoint`` accepts a tz-aware UTC value."""
    point = BenchmarkPoint(
        date=datetime(2026, 5, 20, 11, 0, tzinfo=UTC), value=Decimal("203450.0000")
    )
    assert point.value == Decimal("203450.0000")


def test_strategy_report_margin_usage_defaults_to_nulls() -> None:
    """csm-set emits an empty ``MarginUsage`` — every field is ``None``."""
    margin = MarginUsage()

    assert margin.avg_margin_used is None
    assert margin.max_margin_used is None
    assert margin.margin_efficiency is None
    assert margin.margin_calls is None


def test_runup_intrabar_fields_optional() -> None:
    """Daily-only strategies omit intrabar fields — both default to ``None``."""
    row = RunUpRow(
        avg_duration_days=Decimal("3.5"),
        avg_runup=Decimal("600.00"),
        max_runup_close_to_close=Decimal("1500.00"),
    )
    assert row.max_runup_intrabar is None
    assert row.max_runup_pct_initial_capital_intrabar is None


def test_strategy_report_ignores_extra_fields() -> None:
    """Forward-compatibility: unknown fields are ignored at validation."""
    payload = json.loads(_full_report().model_dump_json())
    payload["unknown_future_field"] = {"x": 1}
    payload["headline"]["another_new_field"] = "ok"

    parsed = StrategyReport.model_validate(payload)
    assert parsed.headline.total_trades == 46


def test_pnl_distribution_bucket_kind_rejects_invalid() -> None:
    """``kind`` is a closed Literal — random strings are rejected."""
    with pytest.raises(ValidationError):
        PnLDistributionBucket.model_validate(
            {
                "bucket_low_pct": "-0.03",
                "bucket_high_pct": "-0.02",
                "count": 1,
                "kind": "weird",
            }
        )


def test_win_loss_split_rejects_negative_counts() -> None:
    """``WinLossSplit`` counts are ``ge=0``."""
    with pytest.raises(ValidationError):
        WinLossSplit.model_validate({"wins": -1, "losses": 1, "breakeven": 0})


def test_capital_efficiency_requires_all_three_scopes() -> None:
    """``capital_usage`` must include every PositionScope key."""
    cap = CapitalEfficiency(
        capital_usage={
            "all": _capital_usage_row(),
            "long": _capital_usage_row(),
            "short": _capital_usage_row(),
        },
        margin_usage=MarginUsage(),
    )
    assert set(cap.capital_usage.keys()) == {"all", "long", "short"}


def test_strategy_report_benchmark_comparison_optional() -> None:
    """``benchmark_comparison`` can be omitted entirely."""
    report = _full_report()
    payload = json.loads(report.model_dump_json())
    payload.pop("benchmark_comparison")

    parsed = StrategyReport.model_validate(payload)
    assert parsed.benchmark_comparison is None
