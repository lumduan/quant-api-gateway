"""Pydantic models for the strategy registry (``strategies.json``)."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class StrategyConfig(BaseModel):
    """Configuration for a single Strategy Service that the gateway aggregates."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    id: str = Field(description="Unique strategy identifier", min_length=1)
    name: str = Field(description="Human-readable strategy name", min_length=1)
    type: str = Field(
        description=(
            "Strategy type discriminator consumed by the dashboard's "
            "StrategyAdapterFactory (e.g. ``EQUITY_MOMENTUM`` -> CSMSetAdapter). "
            "Required so misconfigured registries fail at startup rather than "
            "silently falling back to the generic adapter in the browser."
        ),
        min_length=1,
    )
    service_url: str = Field(
        description="Base URL of the upstream Strategy Service",
        min_length=1,
    )
    capital_weight: Decimal = Field(
        description="Allocation weight used by the aggregator",
        ge=0,
        max_digits=8,
        decimal_places=4,
    )
    active: bool = Field(
        default=True,
        description="Whether this strategy participates in the daily aggregation round",
    )


class StrategyRegistry(BaseModel):
    """The full strategy registry as loaded from ``strategies.json``."""

    model_config = ConfigDict(frozen=True)

    strategies: list[StrategyConfig] = Field(description="All registered strategies")

    def active_strategies(self) -> list[StrategyConfig]:
        """Return only the entries with ``active=True``."""
        return [s for s in self.strategies if s.active]

    def by_id(self, strategy_id: str) -> StrategyConfig | None:
        """Return the strategy with the matching id, or ``None`` if not found."""
        for s in self.strategies:
            if s.id == strategy_id:
                return s
        return None
