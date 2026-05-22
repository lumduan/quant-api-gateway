"""``GET /api/v2/engines/signals/*`` — signals engine stubs.

Stub endpoints only — the signals engine is dormant. Real signal-generation
logic is integrated in a future phase.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["v2-signals"])


class SignalsHealthResponse(BaseModel):
    status: str = Field(description="Engine health status")
    engine: str = Field(description="Engine slug")


class SignalsStatusResponse(BaseModel):
    status: str = Field(description="Engine status (active, dormant, etc.)")
    message: str = Field(description="Human-readable status message")


@router.get(
    "/health",
    response_model=SignalsHealthResponse,
    summary="Signals engine health (stub)",
)
async def signals_health() -> SignalsHealthResponse:
    """Return a stub health check for the signals engine."""
    return SignalsHealthResponse(status="stub", engine="signals")


@router.get(
    "/status",
    response_model=SignalsStatusResponse,
    summary="Signals engine status (stub)",
)
async def signals_status() -> SignalsStatusResponse:
    """Return the current signals engine status (stub — dormant)."""
    return SignalsStatusResponse(status="dormant", message="signal engine not yet active")
