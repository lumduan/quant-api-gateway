"""FastAPI application entrypoint.

Wires up structured JSON logging, request-ID middleware, the async lifespan
(database / Redis / strategy registry), ``GET /health``, and the v1 router
mounted under ``/api/v1``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.api.v1.router import api_router
from src.config import get_settings
from src.db.postgres import close_pool, get_pool
from src.db.redis_client import close_redis, get_redis
from src.logging_config import configure_logging, request_id_var
from src.services import strategy_registry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown.

    Loads the strategy registry from ``strategies.json`` (path configurable via
    ``Settings.strategy_registry_path``) and opens the asyncpg pool and Redis
    connection eagerly so the first request does not pay first-call latency.
    Mongo stays lazy until a later phase needs it.

    Yields:
        Control to the running application. The generator resumes on shutdown
        to release the pool and clear the in-memory registry.
    """
    settings = get_settings()
    configure_logging(settings)
    logger.info("quant-api-gateway starting up")
    registry = strategy_registry.load_registry(settings.strategy_registry_path)
    strategy_registry.set_registry(registry)
    await get_pool()
    await get_redis()
    try:
        yield
    finally:
        logger.info("quant-api-gateway shutting down")
        await close_pool()
        await close_redis()
        strategy_registry.clear_registry()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique ``X-Request-ID`` header to every response.

    A :func:`uuid4` is generated per request, stored on
    ``request.state.request_id`` and in the :data:`request_id_var` context
    variable so the structured logger can include it. The same id is echoed
    back to the caller in the ``X-Request-ID`` response header.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(token)


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


app.add_middleware(RequestIDMiddleware)
app.include_router(api_router, prefix="/api/v1")
