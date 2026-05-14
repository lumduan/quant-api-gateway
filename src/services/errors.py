"""Typed exceptions raised by ``src.services``."""


class ServiceError(Exception):
    """Root exception for every service-layer failure."""


class StrategyRegistryLoadError(ServiceError):
    """Raised when ``strategies.json`` cannot be read, parsed, or validated."""


class UnknownStrategyError(ServiceError):
    """Raised when a payload references a strategy id missing from the registry."""


class IngestionPersistError(ServiceError):
    """Raised when persisting a ``daily_performance`` row to Postgres fails."""
