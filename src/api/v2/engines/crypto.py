"""``GET /api/v2/engines/crypto/*`` — proxy to the Crypto capture engine.

The standalone ``quant-crypto-engine`` (host ``:9100``, in-network
``http://quant-crypto-engine:8000``) is a market-data-plane sibling to
``quant-orderbook-engine`` / ``quant-ticker-engine``: it durably captures 24/7
crypto L2 depth + time & sales over one WebSocket per venue (Binance TH /
Binance Global / Bitkub) and grades each per-venue day. The gateway is a
**thin, read-only reverse proxy**: it holds no credential, forwards only the
caller's ``X-API-Key``, and maps transport failures to clean ``502/503/504``
instead of leaking a stack trace. Every route is a GET — the crypto engine is a
pure data plane (D1/CX1); orders never flow through it. Engine 4xx responses
(404/422 envelopes) pass through verbatim.

Proxied surface (all GET — the crypto engine's Phase-1 read API):

* ``/health`` — engine liveness (+ today's binance_th DQ grade).
* ``/status`` — per-venue capture state + DB-writer stats.
* ``/symbols`` — configured per-venue instrument universe.
* ``/premium`` — cross-exchange THB premium (a derived VIEW; bps + basis per pair).
* ``/trades/{symbol}`` — recent trade prints (``?venue=`` filter). ``symbol`` is
  a ``:path`` param — crypto symbols contain a slash (e.g. ``BTC/USDT``).
* ``/manifest/{day}`` — per-``(day, venue)`` DQ manifest (``?source=`` venue).

The crypto engine has **no** ``/order-book`` and **no** SSE (Phase 3+ on the
engine), so this proxy is JSON-only — no ``_proxy_sse``.
"""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-crypto"])

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared upstream client, creating it lazily (connection reuse)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=settings.crypto_engine_service_url,
            timeout=settings.crypto_engine_timeout_seconds,
        )
    return _client


async def close_crypto_client() -> None:
    """Close the shared upstream client (called from the app lifespan)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _proxy(request: Request, path: str) -> JSONResponse:
    """Forward a GET to the upstream engine, mapping transport failures cleanly.

    Read-only: only the caller's ``X-API-Key`` is forwarded — no body, no
    ``X-Strategy-Id``.
    """
    client = _get_client()
    headers: dict[str, str] = {}
    api_key = request.headers.get("X-API-Key")
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        upstream = await client.get(path, params=dict(request.query_params), headers=headers)
    except httpx.TimeoutException as exc:
        logger.warning("crypto upstream timeout for GET %s", path)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="crypto engine timed out",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("crypto upstream unavailable for GET %s: %s", path, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="crypto engine unavailable",
        ) from exc

    if upstream.status_code >= 500:
        logger.warning("crypto upstream %d for GET %s", upstream.status_code, path)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="crypto engine error",
        )
    try:
        payload: Any = upstream.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="invalid crypto engine response",
        ) from exc
    # Forward upstream status (incl. 4xx auth/validation envelopes) and body verbatim.
    return JSONResponse(status_code=upstream.status_code, content=payload)


@router.get("/health", summary="Crypto engine health (proxied)")
async def crypto_health(request: Request) -> JSONResponse:
    """Proxy the engine's liveness payload (today's binance_th DQ grade included)."""
    return await _proxy(request, "/health")


@router.get("/status", summary="Per-venue capture status (proxied)")
async def crypto_status(request: Request) -> JSONResponse:
    """Proxy the engine's per-venue capture state + DB-writer stats."""
    return await _proxy(request, "/status")


@router.get("/symbols", summary="Configured symbol universe (proxied)")
async def crypto_symbols(request: Request) -> JSONResponse:
    """Proxy the configured per-venue instrument universe."""
    return await _proxy(request, "/symbols")


@router.get("/premium", summary="Cross-exchange THB premium (proxied)")
async def crypto_premium(request: Request) -> JSONResponse:
    """Proxy the engine's cross-exchange THB-premium read (a derived VIEW; ephemeral)."""
    return await _proxy(request, "/premium")


@router.get("/trades/{symbol:path}", summary="Recent trade prints (proxied)")
async def crypto_trades(symbol: str, request: Request) -> JSONResponse:
    """Proxy the recent trade-prints read for a symbol (``:path`` — symbols contain ``/``)."""
    return await _proxy(request, f"/trades/{symbol}")


@router.get("/manifest/{day}", summary="Per-day, per-venue DQ manifest (proxied)")
async def crypto_manifest(day: str, request: Request) -> JSONResponse:
    """Proxy the per-``(day, venue)`` data-quality manifest (``?source=`` selects the venue)."""
    return await _proxy(request, f"/manifest/{day}")
