import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from src.schemas.strategy_report import StrategyReport

logger = logging.getLogger(__name__)


class StrategyMetadata(BaseModel):
    """Strategy identification metadata sent by every Strategy Service."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    id: str = Field(description="Unique strategy identifier", min_length=1)
    type: str = Field(description="Strategy type classification", min_length=1)
    last_updated: datetime = Field(description="UTC timestamp of last update")

    @field_validator("last_updated")
    @classmethod
    def _enforce_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC required)")
        if v.tzinfo != UTC:
            raise ValueError(f"datetime must be UTC, got {v.tzinfo}")
        return v


class EquityPoint(BaseModel):
    """A single (date, value) point in an equity curve."""

    model_config = ConfigDict(frozen=True)

    date: str = Field(
        description="ISO 8601 date (YYYY-MM-DD)",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    value: Decimal = Field(
        description="Equity value at close on this date",
        max_digits=18,
        decimal_places=4,
    )


class PerformanceMetrics(BaseModel):
    """Performance metrics for a single reporting period."""

    model_config = ConfigDict(frozen=True)

    daily_pnl: Decimal = Field(
        description="Daily profit and loss",
        max_digits=18,
        decimal_places=4,
    )
    equity_curve: list[EquityPoint] = Field(
        description="Full equity curve as list of points",
        min_length=1,
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

    @field_validator("max_drawdown")
    @classmethod
    def _max_drawdown_not_positive(cls, v: Decimal) -> Decimal:
        if v > 0:
            raise ValueError(f"max_drawdown must be ≤ 0, got {v}")
        return v


class CurrentExposure(BaseModel):
    """Snapshot of current positions and capital."""

    model_config = ConfigDict(frozen=True)

    total_value: Decimal = Field(
        description="Total portfolio value",
        max_digits=18,
        decimal_places=4,
        ge=0,
    )
    cash_balance: Decimal = Field(
        description="Cash balance",
        max_digits=18,
        decimal_places=4,
        ge=0,
    )
    positions_count: int = Field(description="Number of open positions", ge=0)


class StrategyPayload(BaseModel):
    """Standard JSON contract that every Strategy Service POSTs to the gateway.

    The ``extended_data`` blob is forward-compatible: any strategy may attach
    arbitrary keys, and the gateway preserves them in the
    ``daily_performance.metadata`` JSONB column. When a ``report`` key is
    present, the post-init validator attempts to parse it through
    :class:`~src.schemas.strategy_report.StrategyReport` and attaches the
    parsed value to the private :attr:`_parsed_report` attribute so the
    ingestion service can persist it into ``strategy_report_snapshot``
    without re-parsing the dict. Invalid reports are not fatal — they log a
    WARNING and leave :attr:`_parsed_report` as ``None`` so ingestion of the
    base payload still succeeds.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    strategy_metadata: StrategyMetadata = Field(description="Strategy identification metadata")
    performance_metrics: PerformanceMetrics = Field(
        description="Performance metrics for the reporting period"
    )
    current_exposure: CurrentExposure = Field(description="Current exposure snapshot")
    extended_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-specific extension data",
    )

    _parsed_report: StrategyReport | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _parse_extended_report(self) -> "StrategyPayload":
        """Parse ``extended_data['report']`` into :class:`StrategyReport`.

        Best-effort: any failure to coerce the report into the typed model is
        logged at WARNING and the payload is accepted with
        :attr:`_parsed_report` left as ``None``. This keeps ingestion
        backward-compatible with strategies that have not yet emitted the
        report block.

        Returns:
            ``self`` (Pydantic v2 convention for ``mode="after"`` validators).
        """
        raw = self.extended_data.get("report")
        if raw is None:
            return self
        try:
            self._parsed_report = StrategyReport.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning(
                "extended_data.report failed to parse for strategy_id=%s: %s",
                self.strategy_metadata.id,
                exc,
            )
            self._parsed_report = None
        return self

    @property
    def parsed_report(self) -> StrategyReport | None:
        """Return the parsed :class:`StrategyReport`, or ``None`` if absent / invalid."""
        return self._parsed_report
