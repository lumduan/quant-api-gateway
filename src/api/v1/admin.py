"""``POST /api/v1/admin/cache/flush`` — guarded admin endpoint.

Requires the ``X-API-Key`` header (same internal API key used by the
ingestion endpoint). Flushes every gateway-owned cache key via
:func:`src.services.cache_invalidator.flush_all`.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.v1.dependencies import verify_api_key
from src.services.cache_invalidator import flush_all
from src.services.errors import CacheError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_api_key)],
)


@router.post(
    "/cache/flush",
    summary="Flush every gateway-owned cache key",
    description=(
        "Deletes all keys matching ``gateway:*`` from Redis. Requires the "
        "``X-API-Key`` header set to the internal API key."
    ),
)
async def flush_cache() -> dict[str, object]:
    """Flush every gateway-owned cache key and return the count."""
    try:
        count = await flush_all()
    except CacheError as exc:
        logger.exception("cache flush failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="cache flush failed",
        ) from exc
    logger.info("admin flushed %d cache keys", count)
    return {"status": "flushed", "keys_deleted": count}
