"""``GET /api/v2/engines/catalog`` — engine registry endpoint.

Returns the list of registered engines. Attempts to read from the
``engine_registry`` table first; falls back to a static list if the
table does not exist yet.
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.db.postgres import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-catalog"])


class EngineEntry(BaseModel):
    """A single registered engine entry."""

    slug: str = Field(description="Unique engine slug")
    type: str = Field(description="INTERNAL or EXTERNAL")
    status: str = Field(description="active, dormant, etc.")
    description: str = Field(description="Human-readable description")


_CATALOG_SQL = """
SELECT slug, type, status, description
FROM engine_registry
ORDER BY id ASC
"""

_STATIC_CATALOG: list[EngineEntry] = [
    EngineEntry(
        slug="market-data",
        type="EXTERNAL",
        status="active",
        description=(
            "Standalone quant-marketdata-engine (host :8300), gateway-proxied; "
            "canonical OHLCV via settfex + tvkit"
        ),
    ),
    EngineEntry(
        slug="backtest",
        type="EXTERNAL",
        status="active",
        description="Wraps csm-set walk-forward backtesting",
    ),
    EngineEntry(
        slug="portfolio",
        type="INTERNAL",
        status="active",
        description="Aggregation, snapshots, equity curves, strategy reports",
    ),
    EngineEntry(
        slug="signals",
        type="EXTERNAL",
        status="dormant",
        description="Signal generation pipeline (future)",
    ),
    EngineEntry(
        slug="execution",
        type="EXTERNAL",
        status="active",
        description=(
            "Standalone quant-execution-engine (host :8400), gateway-proxied; "
            "canonical order router (sim-first), no broker credential in the gateway"
        ),
    ),
]


@router.get(
    "/catalog",
    response_model=list[EngineEntry],
    summary="List all registered engines",
    description=(
        "Returns the engine registry from the ``engine_registry`` database table. "
        "If the table does not exist yet, falls back to a static catalog of the "
        "four known engines."
    ),
)
async def get_catalog() -> list[EngineEntry]:
    """Return the engine catalog — DB-first, static fallback."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_CATALOG_SQL)
        if rows:
            return [
                EngineEntry(
                    slug=row["slug"],
                    type=row["type"],
                    status=row["status"],
                    description=row["description"],
                )
                for row in rows
            ]
    except Exception:
        logger.warning(
            "engine_registry table unavailable; falling back to static catalog",
            exc_info=True,
        )

    return _STATIC_CATALOG
