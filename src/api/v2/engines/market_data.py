"""``GET /api/v2/engines/market-data/*`` — proxy to the Market Data engine.

The standalone ``quant-marketdata-engine`` (host ``:8300``, in-network
``http://quant-marketdata-engine:8000``) is the canonical OHLCV producer and the
sole owner of the tvkit cookie. The gateway is a **thin reverse proxy**: it holds
no credential, forwards the read contract (``/health``, ``/ohlcv``,
``/ohlcv/adjusted``, ``/universe``) plus the caller's ``X-API-Key``, and maps
upstream failures to clean ``502/503/504`` instead of leaking a stack trace.

``/providers`` is retained as a static informational endpoint for backward
compatibility with the previous stub.
"""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-market-data"])

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared upstream client, creating it lazily (connection reuse)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=settings.marketdata_engine_service_url,
            timeout=settings.marketdata_engine_timeout_seconds,
        )
    return _client


async def close_market_data_client() -> None:
    """Close the shared upstream client (called from the app lifespan)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class MarketDataProvidersResponse(BaseModel):
    providers: list[str] = Field(description="Registered data providers")
    status: str = Field(description="Provider aggregate status")


async def _proxy(path: str, request: Request) -> JSONResponse:
    """Forward a GET to the upstream engine, mapping transport failures cleanly."""
    client = _get_client()
    headers: dict[str, str] = {}
    api_key = request.headers.get("X-API-Key")
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        upstream = await client.get(path, params=dict(request.query_params), headers=headers)
    except httpx.TimeoutException as exc:
        logger.warning("market-data upstream timeout for %s", path)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="market-data engine timed out",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("market-data upstream unavailable for %s: %s", path, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="market-data engine unavailable",
        ) from exc

    if upstream.status_code >= 500:
        logger.warning("market-data upstream %d for %s", upstream.status_code, path)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="market-data engine error",
        )
    try:
        payload: Any = upstream.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="invalid market-data engine response",
        ) from exc
    # Forward upstream status (incl. 4xx auth/validation) and body verbatim.
    return JSONResponse(status_code=upstream.status_code, content=payload)


@router.get("/health", summary="Market-data engine health (proxied)")
async def market_data_health(request: Request) -> JSONResponse:
    """Proxy the upstream engine's readiness payload."""
    return await _proxy("/health", request)


@router.get("/ohlcv", summary="Raw OHLCV bars (proxied)")
async def market_data_ohlcv(request: Request) -> JSONResponse:
    """Proxy a raw OHLCV read to the engine."""
    return await _proxy("/ohlcv", request)


@router.get("/ohlcv/adjusted", summary="Adjust-on-read OHLCV bars (proxied)")
async def market_data_ohlcv_adjusted(request: Request) -> JSONResponse:
    """Proxy an adjust-on-read OHLCV read to the engine."""
    return await _proxy("/ohlcv/adjusted", request)


@router.get("/universe", summary="Point-in-time index constituents (proxied)")
async def market_data_universe(request: Request) -> JSONResponse:
    """Proxy a universe read to the engine."""
    return await _proxy("/universe", request)


@router.get(
    "/providers",
    response_model=MarketDataProvidersResponse,
    summary="Registered data providers (informational)",
)
async def market_data_providers() -> MarketDataProvidersResponse:
    """Return the engine's upstream data providers (static, informational)."""
    return MarketDataProvidersResponse(providers=["settfex", "tvkit"], status="active")
