"""``/api/v2/engines/execution/*`` — proxy to the Execution engine.

The standalone ``quant-execution-engine`` (host ``:8400``, in-network
``http://quant-execution-engine:8000``) is the canonical order router and the
sole owner of broker order-routing credentials. The gateway is a **thin
reverse proxy**: it holds no credential, forwards the Phase-2 order surface
(``/health``, ``/capabilities``, ``POST /orders``, ``GET/DELETE
/orders/{client_order_id}``) plus the caller's ``X-API-Key``, and maps
transport failures to clean ``502/503/504``. Engine 4xx responses — including
the typed rejection envelopes (``public_mode``, ``risk_rejected``,
``capability_unsupported``, ``kill_switch_engaged`` …) — pass through
verbatim. The engine's ``/admin/*`` (kill-switch) surface is deliberately
NOT proxied: owner-mode operations are engine-direct only.
"""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-execution"])

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared upstream client, creating it lazily (connection reuse)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=settings.execution_engine_service_url,
            timeout=settings.execution_engine_timeout_seconds,
        )
    return _client


async def close_execution_client() -> None:
    """Close the shared upstream client (called from the app lifespan)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _proxy(request: Request, method: str, path: str) -> JSONResponse:
    """Forward the request (incl. raw body) upstream, mapping failures cleanly."""
    client = _get_client()
    headers: dict[str, str] = {}
    api_key = request.headers.get("X-API-Key")
    if api_key:
        headers["X-API-Key"] = api_key
    body = await request.body()
    if body:
        headers["Content-Type"] = request.headers.get("Content-Type", "application/json")
    try:
        upstream = await client.request(
            method,
            path,
            params=dict(request.query_params),
            headers=headers,
            content=body or None,
        )
    except httpx.TimeoutException as exc:
        logger.warning("execution upstream timeout for %s %s", method, path)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="execution engine timed out",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("execution upstream unavailable for %s %s: %s", method, path, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="execution engine unavailable",
        ) from exc

    if upstream.status_code >= 500:
        logger.warning("execution upstream %d for %s %s", upstream.status_code, method, path)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="execution engine error",
        )
    try:
        payload: Any = upstream.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="invalid execution engine response",
        ) from exc
    # Forward upstream status (incl. typed 4xx envelopes) and body verbatim.
    return JSONResponse(status_code=upstream.status_code, content=payload)


@router.get("/health", summary="Execution engine health (proxied)")
async def execution_health(request: Request) -> JSONResponse:
    """Proxy the engine's liveness payload (stage + public_mode included)."""
    return await _proxy(request, "GET", "/health")


@router.get("/capabilities", summary="Declared broker capability matrix (proxied)")
async def execution_capabilities(request: Request) -> JSONResponse:
    """Proxy the per-(broker, market) capability sets."""
    return await _proxy(request, "GET", "/capabilities")


@router.post(
    "/orders",
    status_code=status.HTTP_201_CREATED,
    summary="Submit a NormalizedOrder (proxied; idempotent on client_order_id)",
)
async def execution_submit_order(request: Request) -> JSONResponse:
    """Forward the order body verbatim; 201 on accept, 200 on idempotent resend."""
    return await _proxy(request, "POST", "/orders")


@router.get("/orders/{client_order_id}", summary="Read one order's normalized state (proxied)")
async def execution_get_order(client_order_id: str, request: Request) -> JSONResponse:
    """Proxy the aggregate order read."""
    return await _proxy(request, "GET", f"/orders/{client_order_id}")


@router.delete("/orders/{client_order_id}", summary="Cancel a resting order (proxied)")
async def execution_cancel_order(client_order_id: str, request: Request) -> JSONResponse:
    """Proxy the cancel."""
    return await _proxy(request, "DELETE", f"/orders/{client_order_id}")
