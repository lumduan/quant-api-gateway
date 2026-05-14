"""FastAPI application entrypoint.

Phase 1 wires up:

* an ``async`` lifespan as the future home of database / Redis / HTTP
  connection setup (no-op in Phase 1);
* a root-level ``GET /health`` endpoint used by the Docker Compose
  healthcheck;
* the v1 router mounted under ``/api/v1`` (sub-routers are attached in
  later phases).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.v1.router import api_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown.

    Phase 1 has no I/O resources to open; later phases open the asyncpg
    pool, motor client, Redis connection, and shared ``httpx.AsyncClient``
    here.

    Args:
        _app: The :class:`fastapi.FastAPI` instance. Unused in Phase 1 but
            retained for the lifespan-handler signature FastAPI expects.

    Yields:
        Control to the running application. The generator resumes on
        shutdown to release resources opened during startup.

    Example:
        >>> from fastapi import FastAPI
        >>> from src.main import lifespan
        >>> app = FastAPI(lifespan=lifespan)
    """
    logger.info("quant-api-gateway starting up")
    try:
        yield
    finally:
        logger.info("quant-api-gateway shutting down")


app = FastAPI(
    title="Quant API Gateway",
    version="1.0.0",
    description=(
        "Central Aggregator Service for the Quant Trading System. Ingests "
        "Daily Performance reports from Strategy Services, computes "
        "weighted return and combined drawdown, caches results in Redis, "
        "and exposes a versioned REST API."
    ),
    lifespan=lifespan,
)


@app.get(
    "/health",
    summary="Liveness probe",
    description=(
        'Returns ``{"status": "ok"}`` whenever the FastAPI app is up. '
        "Used by the Docker Compose healthcheck."
    ),
    response_model=dict[str, str],
    tags=["meta"],
)
async def health() -> dict[str, str]:
    """Report process liveness.

    Returns:
        A two-key mapping ``{"status": "ok"}``. The endpoint never returns
        a non-200 status code — readiness checks (database/Redis) belong on
        a future ``/ready`` endpoint introduced when connections are wired
        in Phase 2.

    Example:
        >>> import httpx
        >>> r = httpx.get("http://localhost:8000/health")
        >>> r.json()
        {'status': 'ok'}
    """
    return {"status": "ok"}


app.include_router(api_router, prefix="/api/v1")
