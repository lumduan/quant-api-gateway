"""Runtime configuration via Pydantic Settings.

Settings are loaded from environment variables (or a local ``.env`` file
during development). The module exposes a lazy ``get_settings()`` rather than
a module-level singleton so tests can override environment variables before
the first read.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application settings.

    Every field is required at runtime except ``log_level``, which defaults
    to ``INFO``. Field descriptions are deliberately verbose so that an
    operator reading ``GET /docs`` (or ``Settings.model_json_schema()``)
    learns the role of each variable without consulting the source.
    """

    postgres_dsn: str = Field(
        ...,
        description=(
            "PostgreSQL DSN for the ``db_gateway`` database provisioned by "
            "quant-infra-db. Example: "
            "``postgresql://postgres:pass@quant-postgres:5432/db_gateway``."
        ),
    )
    mongo_uri: str = Field(
        ...,
        description=(
            "MongoDB connection URI used for extended/document storage. "
            "Example: ``mongodb://quant-mongo:27017/``."
        ),
    )
    redis_url: str = Field(
        ...,
        description=(
            "Redis URL used for the gateway's aggregation cache. Example: "
            "``redis://quant-redis:6379/0``."
        ),
    )
    csm_set_service_url: str = Field(
        ...,
        description=(
            "Base URL of the upstream CSM-SET Strategy Service. Example: "
            "``http://quant-csm-set:8001``."
        ),
    )
    marketdata_engine_service_url: str = Field(
        default="http://quant-marketdata-engine:8000",
        description=(
            "Base URL of the upstream Market Data engine that the gateway proxies "
            "``/api/v2/engines/market-data/*`` to. Defaults to the in-network "
            "service name; override only for local/host access."
        ),
    )
    marketdata_engine_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Per-request timeout (seconds) for upstream Market Data engine calls.",
    )
    execution_engine_service_url: str = Field(
        default="http://quant-execution-engine:8000",
        description=(
            "Base URL of the upstream Execution engine that the gateway proxies "
            "``/api/v2/engines/execution/*`` to. Defaults to the in-network "
            "service name; override only for local/host access. The gateway "
            "holds NO broker credential — it forwards orders, nothing more."
        ),
    )
    execution_engine_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Per-request timeout (seconds) for upstream Execution engine calls.",
    )
    internal_api_key: str = Field(
        ...,
        min_length=1,
        description=(
            "Shared secret presented as the ``X-API-Key`` header by every "
            "Strategy Service when posting to the ingestion endpoint."
        ),
    )
    log_level: str = Field(
        default="INFO",
        description=("Python ``logging`` level name (e.g. ``DEBUG``, ``INFO``, ``WARNING``)."),
    )
    strategy_registry_path: Path = Field(
        default=Path("strategies.json"),
        description=(
            "Filesystem path to the strategy registry JSON file loaded at startup. "
            "Relative paths are resolved against the working directory."
        ),
    )
    overall_performance_ttl_seconds: int = Field(
        default=300,
        description="TTL in seconds for the ``overall_performance`` cache key.",
    )
    strategy_performance_ttl_seconds: int = Field(
        default=300,
        description="TTL in seconds for ``strategy:{id}:performance`` cache keys.",
    )
    portfolio_snapshot_ttl_seconds: int = Field(
        default=3600,
        description="TTL in seconds for ``portfolio_snapshot:{date}`` cache keys.",
    )
    csm_set_dsn: str = Field(
        ...,
        min_length=1,
        description=(
            "Read-only PostgreSQL DSN for the ``db_csm_set`` database "
            "provisioned by quant-infra-db. Used by the strategy-report "
            "service to read ``trade_history`` and "
            "``benchmark_equity_curve``. Example: "
            "``postgresql://gateway_ro:pass@quant-postgres:5432/db_csm_set``."
        ),
    )
    strategy_report_ttl_seconds: int = Field(
        default=600,
        description=("TTL in seconds for ``gateway:strategy:{id}:report:*`` cache keys."),
    )
    trade_log_ttl_seconds: int = Field(
        default=300,
        description=("TTL in seconds for ``gateway:strategy:{id}:trades:*`` cache keys."),
    )
    benchmark_curve_ttl_seconds: int = Field(
        default=600,
        description=("TTL in seconds for ``gateway:strategy:{id}:benchmark:*`` cache keys."),
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` instance.

    Settings are read once from the environment (or the local ``.env``) on
    first access and reused for subsequent calls. Tests that need to
    override environment variables should call
    ``get_settings.cache_clear()`` after mutating the environment.

    Returns:
        The validated :class:`Settings` instance.

    Raises:
        pydantic.ValidationError: If any required environment variable is
            missing or fails its validation rule.

    Example:
        >>> from src.config import get_settings
        >>> settings = get_settings()
        >>> settings.log_level
        'INFO'
    """
    return Settings()  # type: ignore[call-arg]
