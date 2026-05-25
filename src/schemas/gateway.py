from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _enforce_utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (UTC required)")
    if v.tzinfo != UTC:
        raise ValueError(f"datetime must be UTC, got {v.tzinfo}")
    return v


class StrategyPerformanceResponse(BaseModel):
    """Per-strategy performance snapshot returned to the Dashboard."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    strategy_id: str = Field(description="Strategy identifier", min_length=1)
    daily_pnl: Decimal = Field(
        description="Latest daily PnL",
        max_digits=18,
        decimal_places=4,
    )
    total_value: Decimal = Field(
        description="Latest total portfolio value",
        max_digits=18,
        decimal_places=4,
        ge=0,
    )
    max_drawdown: Decimal = Field(
        description="Maximum drawdown as a negative or zero percentage (e.g. -0.063)",
        max_digits=8,
        decimal_places=4,
    )
    sharpe_ratio: Decimal = Field(
        description="Sharpe ratio",
        max_digits=8,
        decimal_places=4,
    )
    last_updated: datetime = Field(description="UTC timestamp of latest data")

    @field_validator("last_updated")
    @classmethod
    def _enforce_utc_last_updated(cls, v: datetime) -> datetime:
        return _enforce_utc(v)


class OverallPerformanceResponse(BaseModel):
    """Aggregated portfolio performance returned to the Dashboard."""

    model_config = ConfigDict(frozen=True)

    total_portfolio_value: Decimal = Field(
        description="Sum of all strategy total_values",
        max_digits=18,
        decimal_places=4,
        ge=0,
    )
    weighted_daily_return: Decimal = Field(
        description="Capital-weighted daily return in fractional form (e.g. 0.0148 = 1.48%)",
        max_digits=8,
        decimal_places=6,
    )
    combined_max_drawdown: Decimal = Field(
        description="Portfolio-level maximum drawdown",
        max_digits=8,
        decimal_places=4,
    )
    active_strategies: int = Field(description="Count of active strategies", ge=0)
    allocation: dict[str, Decimal] = Field(description="Map of strategy_id to capital weight")
    strategies: list[StrategyPerformanceResponse] = Field(
        description="Per-strategy performance snapshots"
    )
    computed_at: datetime = Field(description="UTC timestamp when this response was computed")

    @field_validator("computed_at")
    @classmethod
    def _enforce_utc_computed_at(cls, v: datetime) -> datetime:
        return _enforce_utc(v)


class PortfolioSnapshotResponse(BaseModel):
    """A single daily portfolio snapshot row."""

    model_config = ConfigDict(frozen=True)

    snapshot_date: date = Field(description="The date this snapshot represents (YYYY-MM-DD)")
    total_portfolio_value: Decimal = Field(
        description="Sum of all strategy total_values for this date",
        max_digits=18,
        decimal_places=4,
        ge=0,
    )
    weighted_daily_return: Decimal = Field(
        description="Capital-weighted daily return for this date",
        max_digits=8,
        decimal_places=6,
    )
    combined_drawdown: Decimal | None = Field(
        default=None,
        description="Portfolio-level maximum drawdown, or null when no equity curves available",
        max_digits=8,
        decimal_places=4,
    )
    active_strategies: int = Field(description="Number of strategies in the snapshot", ge=0)
    allocation: dict[str, Decimal] = Field(
        description="Map of strategy_id to normalized capital weight"
    )
    computed_at: datetime = Field(description="UTC timestamp when the snapshot row was written")


class MetricItem(BaseModel):
    """One metric record, shaped for OpenBB's Metric widget.

    Follows https://docs.openbb.co/workspace/developers/widget-types/metric.
    The widget renders arrows and colors from the sign of ``delta``; this
    payload carries only pre-formatted strings.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    label: str = Field(description="Human-readable metric name", min_length=1)
    value: str = Field(
        description=(
            "Pre-formatted value cell (units embedded, no arrows). E.g. '0.63%', '$998,142.71'."
        )
    )
    delta: str = Field(
        default="",
        description=(
            "Pre-formatted delta cell — plain signed number, no unit, no arrow. "
            "Empty string when no comparable previous snapshot exists, or when "
            "the source field is null on either side."
        ),
    )


class PortfolioMetricsResponse(BaseModel):
    """Cache-internal wrapper around a list of :class:`MetricItem`.

    The HTTP endpoint flattens this to a bare ``list[MetricItem]`` because
    the Metric widget expects an array at the response root. The wrapper
    preserves snapshot metadata for cache identity and debugging.
    """

    model_config = ConfigDict(frozen=True)

    snapshot_date: date = Field(description="Date of this metrics snapshot (YYYY-MM-DD)")
    metrics: list[MetricItem] = Field(
        description="Ordered metric items: Daily Return, Portfolio Drawdown, Total Portfolio Value"
    )
    computed_at: datetime = Field(description="UTC timestamp when this response was computed")

    @field_validator("computed_at")
    @classmethod
    def _enforce_utc_metrics_computed_at(cls, v: datetime) -> datetime:
        return _enforce_utc(v)
