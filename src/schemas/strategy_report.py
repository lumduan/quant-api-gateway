"""Pydantic v2 models for the per-strategy TradingView-style report.

These shapes mirror the JSON contract documented in the umbrella
``feature-strategies-report-metrics`` ROADMAP and the csm-set Pydantic
models at ``strategies/csm-set/src/csm/research/strategy_report_models.py``
1:1. Every monetary, ratio, and percentage value is :class:`decimal.Decimal`
end-to-end and JSON-serialised as a string via
:meth:`pydantic.BaseModel.model_dump_json`.

Design notes:

* Sub-models set ``extra="ignore"`` so additive changes from upstream
  strategies do not break ingestion (the umbrella roadmap calls this
  "versioned by additive optionality").
* Optional fields default to ``None`` so daily-only strategies (csm-set)
  can omit ``*_intrabar`` fields and non-margin strategies can omit
  ``margin_usage`` values, while the parser still validates structurally.
* Every ``datetime`` field is validated to be tz-aware UTC — strategies
  that emit naive timestamps are rejected at the boundary.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PositionScope = Literal["all", "long", "short"]


def _require_utc(value: datetime, *, field_name: str) -> datetime:
    """Enforce UTC tz-awareness on a parsed ``datetime``.

    Args:
        value: The candidate value.
        field_name: The field name used in the error message.

    Returns:
        The same value, unchanged, after validation.

    Raises:
        ValueError: If ``value`` is naive or has a non-UTC tzinfo.
    """
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware (UTC required)")
    if value.tzinfo != UTC:
        raise ValueError(f"{field_name} must be UTC, got {value.tzinfo}")
    return value


class _ReportBase(BaseModel):
    """Common Pydantic config for every report sub-model."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class Headline(_ReportBase):
    """Top-of-page KPIs (Total P&L, Max DD, Total trades, …)."""

    total_pnl: Decimal = Field(description="Total profit and loss in currency units")
    total_pnl_pct: Decimal = Field(description="Total P&L as a fractional return")
    max_equity_drawdown: Decimal = Field(description="Maximum equity drawdown in currency units")
    max_equity_drawdown_pct: Decimal = Field(description="Maximum equity drawdown as a fraction")
    total_trades: int = Field(ge=0, description="Total closed-trade count")
    profitable_trades: int = Field(ge=0, description="Profitable closed-trade count")
    profitable_pct: Decimal = Field(description="Profitable / total ratio (fractional)")
    profit_factor: Decimal = Field(description="Gross profit / gross loss")


class ProfitStructure(_ReportBase):
    """Profit-structure bar-chart values (gross totals)."""

    total_profit: Decimal = Field(description="Sum of profitable trade P&Ls")
    open_pnl: Decimal = Field(description="Sum of open-trade unrealised P&L")
    total_loss: Decimal = Field(description="Sum of losing trade P&Ls (negative)")
    commission: Decimal = Field(description="Commission paid (negative on cost)")
    net_pnl: Decimal = Field(description="Net P&L after commission")


class ReturnsRow(_ReportBase):
    """One row of the returns table (per side or 'all')."""

    initial_capital: Decimal = Field(description="Initial capital deployed for this scope")
    open_pnl: Decimal = Field(description="Open-position P&L")
    net_pnl: Decimal = Field(description="Net realised P&L")
    gross_profit: Decimal = Field(description="Sum of profitable trade P&Ls")
    gross_loss: Decimal = Field(description="Sum of losing trade P&Ls")
    profit_factor: Decimal = Field(description="Gross profit / |gross loss|")
    commission_paid: Decimal = Field(description="Commission paid for trades in this scope")
    expected_payoff: Decimal = Field(description="Average expected P&L per trade")


class Returns(_ReportBase):
    """Returns table broken down by trade side."""

    all: ReturnsRow = Field(description="Across both long and short trades")
    long: ReturnsRow = Field(description="Long-only subset")
    short: ReturnsRow = Field(description="Short-only subset")


class BenchmarkComparison(_ReportBase):
    """Buy-and-hold benchmark comparison summary."""

    buy_and_hold_return: Decimal = Field(description="Absolute buy-and-hold P&L")
    buy_and_hold_pct: Decimal = Field(description="Buy-and-hold return as a fraction")
    strategy_outperformance: Decimal = Field(description="Strategy − buy-and-hold (fractional)")


class RiskAdjusted(_ReportBase):
    """Risk-adjusted return metrics."""

    sharpe_ratio: Decimal = Field(description="Annualised Sharpe ratio")
    sortino_ratio: Decimal = Field(description="Annualised Sortino ratio")


class PnLDistributionBucket(_ReportBase):
    """One bucket of the trade-return histogram."""

    bucket_low_pct: Decimal = Field(description="Lower edge of the bucket (fractional return)")
    bucket_high_pct: Decimal = Field(description="Upper edge of the bucket (fractional return)")
    count: int = Field(ge=0, description="Number of trades inside this bucket")
    kind: Literal["loss", "profit", "breakeven"] = Field(description="Bucket polarity label")


class WinLossSplit(_ReportBase):
    """Donut-chart split of win / loss / breakeven trade counts."""

    wins: int = Field(ge=0, description="Profitable trade count")
    losses: int = Field(ge=0, description="Losing trade count")
    breakeven: int = Field(ge=0, description="Break-even trade count")


class TradesAnalysis(_ReportBase):
    """Histogram + donut summary of trade outcomes."""

    pnl_distribution: list[PnLDistributionBucket] = Field(
        default_factory=list,
        description="Bucketed trade-return distribution",
    )
    win_loss_split: WinLossSplit = Field(description="Donut counts of wins / losses / breakeven")
    avg_loss_pct: Decimal = Field(description="Average losing-trade return (fractional)")
    avg_profit_pct: Decimal = Field(description="Average winning-trade return (fractional)")


class DetailsRow(_ReportBase):
    """One row of the details table (per side or 'all')."""

    total_trades: int = Field(ge=0, description="Total closed trades in this scope")
    total_open_trades: int = Field(ge=0, description="Total open trades in this scope")
    winning_trades: int = Field(ge=0, description="Winning trade count")
    losing_trades: int = Field(ge=0, description="Losing trade count")
    percent_profitable: Decimal = Field(description="Fractional win-rate")
    avg_pnl: Decimal = Field(description="Average P&L per trade")
    avg_winning_trade: Decimal = Field(description="Average P&L of winning trades")
    avg_losing_trade: Decimal = Field(description="Average P&L of losing trades")
    ratio_avg_win_avg_loss: Decimal = Field(description="|avg_win| / |avg_loss|")
    largest_winning_trade: Decimal = Field(description="Largest winning-trade P&L")
    largest_winning_trade_pct: Decimal = Field(description="Largest winning-trade return")
    largest_winner_pct_of_gross_profit: Decimal = Field(description="Largest winner / gross profit")
    largest_losing_trade: Decimal = Field(description="Largest losing-trade P&L")
    largest_losing_trade_pct: Decimal = Field(description="Largest losing-trade return")
    largest_loser_pct_of_gross_loss: Decimal = Field(description="Largest loser / gross loss")
    outliers_count: int = Field(ge=0, description="Count of outlier trades")
    outliers_pnl: Decimal = Field(description="Aggregate P&L from outlier trades")
    avg_bars_in_trades: Decimal = Field(description="Average bars in any trade")
    avg_bars_in_winning_trades: Decimal = Field(description="Average bars in winning trades")
    avg_bars_in_losing_trades: Decimal = Field(description="Average bars in losing trades")


class Details(_ReportBase):
    """Details table broken down by side."""

    all: DetailsRow = Field(description="Across both long and short trades")
    long: DetailsRow = Field(description="Long-only subset")
    short: DetailsRow = Field(description="Short-only subset")


class CapitalUsageRow(_ReportBase):
    """One row of the capital-usage section."""

    annualized_return_cagr: Decimal = Field(description="Compound annual growth rate")
    return_on_initial_capital: Decimal = Field(description="Total return / initial capital")
    account_size_required: Decimal = Field(description="Minimum capital required to run")
    return_on_account_size_required: Decimal = Field(
        description="Total return / account size required"
    )
    net_profit_pct_of_largest_loss: Decimal = Field(
        description="Net profit / largest losing trade",
    )


class MarginUsage(_ReportBase):
    """Margin-usage summary (TFEX-only; all fields ``None`` for csm-set)."""

    avg_margin_used: Decimal | None = Field(default=None, description="Average margin in use")
    max_margin_used: Decimal | None = Field(default=None, description="Peak margin in use")
    margin_efficiency: Decimal | None = Field(
        default=None,
        description="Net P&L / max margin used",
    )
    margin_calls: int | None = Field(
        default=None,
        ge=0,
        description="Count of margin calls during the period",
    )


class CapitalEfficiency(_ReportBase):
    """Capital + margin usage section."""

    capital_usage: dict[PositionScope, CapitalUsageRow] = Field(
        description="Per-scope capital usage rows (keys: all/long/short)"
    )
    margin_usage: MarginUsage = Field(description="Margin usage summary; null fields if N/A")


class RunUpRow(_ReportBase):
    """Run-up statistics — close-to-close and intrabar (intrabar may be ``None``)."""

    avg_duration_days: Decimal = Field(description="Average run-up duration in days")
    avg_runup: Decimal = Field(description="Average run-up magnitude (currency)")
    max_runup_close_to_close: Decimal = Field(description="Max close-to-close run-up")
    max_runup_intrabar: Decimal | None = Field(
        default=None, description="Max intrabar run-up (null for daily-only)"
    )
    max_runup_pct_initial_capital_intrabar: Decimal | None = Field(
        default=None, description="Max intrabar run-up as a fraction of initial capital"
    )


class DrawdownRow(_ReportBase):
    """Drawdown statistics — close-to-close and intrabar (intrabar may be ``None``)."""

    avg_duration_days: Decimal = Field(description="Average drawdown duration in days")
    avg_drawdown: Decimal = Field(description="Average drawdown magnitude (currency)")
    max_drawdown_close_to_close: Decimal = Field(description="Max close-to-close drawdown")
    max_drawdown_intrabar: Decimal | None = Field(
        default=None, description="Max intrabar drawdown (null for daily-only)"
    )
    max_drawdown_pct_initial_capital_intrabar: Decimal | None = Field(
        default=None,
        description="Max intrabar drawdown as a fraction of initial capital",
    )
    return_of_max_drawdown: Decimal = Field(
        description="Return realised between drawdown trough and recovery",
    )


class RunUpsDrawdowns(_ReportBase):
    """Aggregate run-ups + drawdowns block."""

    runups: RunUpRow = Field(description="Run-up statistics")
    drawdowns: DrawdownRow = Field(description="Drawdown statistics")


class TradeLogEntry(_ReportBase):
    """One row of the paginated trade log."""

    entry_time: datetime = Field(description="UTC trade-entry timestamp")
    exit_time: datetime = Field(description="UTC trade-exit timestamp")
    symbol: str = Field(min_length=1, description="Traded symbol")
    side: Literal["LONG", "SHORT"] = Field(description="Trade side")
    qty: Decimal = Field(description="Trade quantity (always positive)")
    entry_price: Decimal = Field(description="Average fill price at entry")
    exit_price: Decimal = Field(description="Average fill price at exit")
    realized_pnl: Decimal = Field(description="Realised P&L for the round-trip")
    duration_bars: int = Field(ge=0, description="Number of bars between entry and exit")
    commission: Decimal = Field(description="Commission paid on the round-trip")

    @field_validator("entry_time")
    @classmethod
    def _entry_time_utc(cls, v: datetime) -> datetime:
        return _require_utc(v, field_name="entry_time")

    @field_validator("exit_time")
    @classmethod
    def _exit_time_utc(cls, v: datetime) -> datetime:
        return _require_utc(v, field_name="exit_time")


class BenchmarkPoint(_ReportBase):
    """One sample of the benchmark equity curve."""

    date: datetime = Field(description="UTC timestamp for this sample")
    value: Decimal = Field(description="Benchmark equity value at this sample")

    @field_validator("date")
    @classmethod
    def _date_utc(cls, v: datetime) -> datetime:
        return _require_utc(v, field_name="date")


class StrategyReport(_ReportBase):
    """Top-level TradingView-style strategy report payload."""

    as_of: datetime = Field(description="UTC timestamp the report was computed at")
    currency: str = Field(
        default="THB", min_length=3, max_length=8, description="Reporting currency code"
    )
    initial_capital: Decimal = Field(description="Initial capital deployed by the strategy")
    headline: Headline = Field(description="Top-of-page KPI strip values")
    profit_structure: ProfitStructure = Field(description="Profit-structure bar chart values")
    returns: Returns = Field(description="Returns table (all / long / short)")
    benchmark_comparison: BenchmarkComparison | None = Field(
        default=None, description="Buy-and-hold comparison summary"
    )
    risk_adjusted: RiskAdjusted = Field(description="Risk-adjusted return metrics")
    trades_analysis: TradesAnalysis = Field(description="Trade P&L distribution and win/loss split")
    details: Details = Field(description="Details table (all / long / short)")
    capital_efficiency: CapitalEfficiency = Field(
        description="Capital + margin usage block",
    )
    runups_drawdowns: RunUpsDrawdowns = Field(description="Run-ups + drawdowns block")
    trades: list[TradeLogEntry] = Field(
        default_factory=list,
        description="Paginated trade log entries",
    )
    benchmark_equity_curve: list[BenchmarkPoint] = Field(
        default_factory=list,
        description="Benchmark equity curve samples (UTC dates)",
    )

    @field_validator("as_of")
    @classmethod
    def _as_of_utc(cls, v: datetime) -> datetime:
        return _require_utc(v, field_name="as_of")


class StrategyReportResponse(BaseModel):
    """Top-level response wrapper for ``GET /strategies/{id}/report``."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(min_length=1, description="Strategy identifier")
    as_of: datetime = Field(description="UTC timestamp the report was computed at")
    report: StrategyReport = Field(description="The TradingView-style strategy report")
    computed_at: datetime = Field(
        description="UTC timestamp the snapshot row was written by the gateway",
    )

    @field_validator("as_of")
    @classmethod
    def _as_of_utc(cls, v: datetime) -> datetime:
        return _require_utc(v, field_name="as_of")

    @field_validator("computed_at")
    @classmethod
    def _computed_at_utc(cls, v: datetime) -> datetime:
        return _require_utc(v, field_name="computed_at")


class TradeLogPage(BaseModel):
    """Paginated trade-log response for ``GET /strategies/{id}/trades``."""

    model_config = ConfigDict(frozen=True)

    items: list[TradeLogEntry] = Field(description="Trade log entries on this page")
    total: int = Field(ge=0, description="Total trade-log row count for this strategy")
    limit: int = Field(ge=1, le=1000, description="Page size requested")
    offset: int = Field(ge=0, description="Page offset requested")


class BenchmarkCurveResponse(BaseModel):
    """Cache-friendly wrapper around a benchmark curve.

    The public API surface returns ``items`` as the bare JSON array
    (``response_model=list[BenchmarkPoint]``) but internally we wrap it in
    this model so the cache-aside helpers — which are Pydantic-bound — can
    serialise / deserialise it without a special case.
    """

    model_config = ConfigDict(frozen=True)

    items: list[BenchmarkPoint] = Field(description="Benchmark equity-curve samples")


__all__: list[str] = [
    "BenchmarkComparison",
    "BenchmarkCurveResponse",
    "BenchmarkPoint",
    "CapitalEfficiency",
    "CapitalUsageRow",
    "Details",
    "DetailsRow",
    "DrawdownRow",
    "Headline",
    "MarginUsage",
    "PnLDistributionBucket",
    "PositionScope",
    "ProfitStructure",
    "Returns",
    "ReturnsRow",
    "RiskAdjusted",
    "RunUpRow",
    "RunUpsDrawdowns",
    "StrategyReport",
    "StrategyReportResponse",
    "TradeLogEntry",
    "TradeLogPage",
    "TradesAnalysis",
    "WinLossSplit",
]
