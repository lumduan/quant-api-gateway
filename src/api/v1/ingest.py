"""``POST /api/v1/ingest/daily-report`` — Strategy Service push endpoint.

Every Strategy Service authenticates via ``X-API-Key`` and POSTs a
:class:`StrategyPayload`. Validation happens at the boundary (FastAPI runs
Pydantic for us). On success we upsert a row into ``daily_performance`` and —
best-effort — try to close out the day's round in ``portfolio_snapshot``.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.api.v1.dependencies import verify_api_key
from src.db.postgres import get_pool
from src.schemas.strategy import StrategyPayload
from src.services import ingestion, snapshot_writer
from src.services.errors import IngestionPersistError, ServiceError
from src.services.strategy_registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ingest",
    tags=["ingest"],
    dependencies=[Depends(verify_api_key)],
)


class IngestAck(BaseModel):
    """Response body for a successful ingestion."""

    status: str
    strategy_id: str
    time: str


@router.post(
    "/daily-report",
    status_code=status.HTTP_201_CREATED,
    response_model=IngestAck,
    summary="Accept a Daily Performance report from a Strategy Service",
    description=(
        "Validates the ``StrategyPayload``, upserts a row into "
        "``db_gateway.daily_performance``, and — once every active strategy "
        "in the registry has reported for today — writes an aggregate row "
        "into ``portfolio_snapshot``. Requires the ``X-API-Key`` header."
    ),
)
async def ingest_daily_report(
    payload: Annotated[StrategyPayload, ...],
) -> IngestAck:
    """Persist ``payload`` and trigger the daily snapshot writer."""
    registry = get_registry()
    strategy_id = payload.strategy_metadata.id
    if registry.by_id(strategy_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown strategy_id: {strategy_id}",
        )

    pool = await get_pool()
    try:
        await ingestion.persist_daily_report(payload, pool=pool)
    except IngestionPersistError as exc:
        logger.exception("ingestion failed for strategy_id=%s", strategy_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to persist daily report",
        ) from exc

    # Best-effort: never let snapshot failure block ingest acknowledgement.
    try:
        await snapshot_writer.maybe_write_snapshot(pool=pool, registry=registry)
    except ServiceError:
        logger.exception("snapshot writer failed after ingest of strategy_id=%s", strategy_id)

    return IngestAck(
        status="accepted",
        strategy_id=strategy_id,
        time=payload.strategy_metadata.last_updated.isoformat(),
    )
