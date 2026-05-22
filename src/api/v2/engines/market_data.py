"""``GET /api/v2/engines/market-data/*`` — market-data engine stubs.

Stub endpoints only — real market-data logic is powered by external services
(settfex, tvkit) and integrated in a future phase.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["v2-market-data"])


class MarketDataHealthResponse(BaseModel):
    status: str = Field(description="Engine health status")
    engine: str = Field(description="Engine slug")


class MarketDataProvidersResponse(BaseModel):
    providers: list[str] = Field(description="Registered data providers")
    status: str = Field(description="Provider aggregate status")


@router.get(
    "/health",
    response_model=MarketDataHealthResponse,
    summary="Market-data engine health (stub)",
)
async def market_data_health() -> MarketDataHealthResponse:
    """Return a stub health check for the market-data engine."""
    return MarketDataHealthResponse(status="stub", engine="market-data")


@router.get(
    "/providers",
    response_model=MarketDataProvidersResponse,
    summary="Registered data providers (stub)",
)
async def market_data_providers() -> MarketDataProvidersResponse:
    """Return the list of registered data providers (stub)."""
    return MarketDataProvidersResponse(providers=["settfex", "tvkit"], status="active")
