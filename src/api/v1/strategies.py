"""``GET /api/v1/strategies`` — registry read endpoint."""

import logging

from fastapi import APIRouter

from src.schemas.registry import StrategyConfig
from src.services.strategy_registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get(
    "",
    response_model=list[StrategyConfig],
    summary="List every active strategy",
    description=(
        "Returns the active entries from ``strategies.json`` as loaded at "
        "application startup. Inactive strategies are filtered out."
    ),
)
async def list_strategies() -> list[StrategyConfig]:
    """Return the active strategies from the in-memory registry."""
    return get_registry().active_strategies()
