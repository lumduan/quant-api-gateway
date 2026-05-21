"""Typed exceptions raised by ``src.services``."""


class ServiceError(Exception):
    """Root exception for every service-layer failure."""


class StrategyRegistryLoadError(ServiceError):
    """Raised when ``strategies.json`` cannot be read, parsed, or validated."""


class UnknownStrategyError(ServiceError):
    """Raised when a payload references a strategy id missing from the registry."""


class IngestionPersistError(ServiceError):
    """Raised when persisting a ``daily_performance`` row to Postgres fails."""


class AggregationError(ServiceError):
    """Raised when aggregation inputs are inconsistent or arithmetic fails."""


class CacheError(ServiceError):
    """Raised when a Redis operation fails (connection, timeout, serialization)."""


class StrategyReportNotFoundError(ServiceError):
    """Raised when no ``strategy_report_snapshot`` row exists for the requested key.

    Carries the requested ``strategy_id`` so the API layer can include it in
    the 404 response body.
    """

    def __init__(self, strategy_id: str, *, date: str | None = None) -> None:
        self.strategy_id = strategy_id
        self.date = date
        scope = f" on {date}" if date else ""
        super().__init__(f"no strategy_report_snapshot for {strategy_id!r}{scope}")
