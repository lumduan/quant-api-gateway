"""``GET /api/v2/engines/orderbook/*`` — proxy to the Order-Book engine.

The standalone ``quant-orderbook-engine`` (host ``:8600``, in-network
``http://quant-orderbook-engine:8000``) is a market-data-plane sibling to
``quant-marketdata-engine``: it durably captures L2 depth + time & sales (via
the Liberator feed) and derives greeks/features. The gateway is a **thin,
read-only reverse proxy**: it holds no credential, forwards only the caller's
``X-API-Key`` (and ``Last-Event-ID`` on streams), and maps transport failures
to clean ``502/503/504`` instead of leaking a stack trace. Every route is a
GET — the order-book engine produces data only; orders never flow through it.
Engine 4xx responses (404/422 envelopes) pass through verbatim.

Proxied surface (all GET):

* ``/health`` — engine liveness (+ today's DQ grade).
* ``/status`` — capture status.
* ``/symbols`` — captured symbol universe.
* ``/order-book/{symbol}`` — JSON L2 snapshot.
* ``/order-book/{symbol}/stream`` — **SSE** order-book updates.
* ``/trades/{symbol}`` — time & sales.
* ``/settlements/{series}`` — daily settlements for a series.
* ``/manifest/{date}`` — per-day DQ manifest.
* ``/greeks`` — option-chain greeks.
* ``/greeks/{symbol}`` — per-symbol greeks.
* ``/features/{symbol}`` — derived microstructure features.

SSE pass-through is unbuffered (chunked transfer; the httpx read timeout is
disabled per-stream so an idle keep-alive-only stream is not killed); JSON
proxying stays buffered.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-orderbook"])

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared upstream client, creating it lazily (connection reuse)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=settings.orderbook_engine_service_url,
            timeout=settings.orderbook_engine_timeout_seconds,
        )
    return _client


async def close_orderbook_client() -> None:
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
        logger.warning("orderbook upstream timeout for GET %s", path)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="orderbook engine timed out",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("orderbook upstream unavailable for GET %s: %s", path, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="orderbook engine unavailable",
        ) from exc

    if upstream.status_code >= 500:
        logger.warning("orderbook upstream %d for GET %s", upstream.status_code, path)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="orderbook engine error",
        )
    try:
        payload: Any = upstream.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="invalid orderbook engine response",
        ) from exc
    # Forward upstream status (incl. 4xx auth/validation envelopes) and body verbatim.
    return JSONResponse(status_code=upstream.status_code, content=payload)


async def _proxy_sse(request: Request, path: str) -> StreamingResponse | JSONResponse:
    """Stream an SSE endpoint upstream unbuffered (chunked transfer).

    Unlike :func:`_proxy`, the upstream response body is **not** read into
    memory: chunks flow through as they arrive so order-book updates reach the
    client immediately and a long-idle stream (keep-alive comment every ~15 s)
    stays open. The per-stream read timeout is disabled (``read=None``) for
    exactly that reason; the connect/write timeout still applies. A client
    disconnect cancels the generator, whose ``finally`` closes the upstream
    response.

    Non-200 upstream responses (the engine's typed envelopes — 404, 503, 422)
    are buffered and returned verbatim as JSON, mirroring :func:`_proxy`.
    """
    client = _get_client()
    settings = get_settings()
    headers: dict[str, str] = {}
    api_key = request.headers.get("X-API-Key")
    if api_key:
        headers["X-API-Key"] = api_key
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    # read=None: an idle SSE stream only emits a keep-alive every ~15 s; the
    # default read timeout would kill it. Connect/write timeouts still apply.
    timeout = httpx.Timeout(settings.orderbook_engine_timeout_seconds, read=None)
    req = client.build_request(
        "GET",
        path,
        params=dict(request.query_params),
        headers=headers,
        timeout=timeout,
    )
    try:
        upstream = await client.send(req, stream=True)
    except httpx.TimeoutException as exc:
        logger.warning("orderbook upstream timeout for GET %s", path)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="orderbook engine timed out",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("orderbook upstream unavailable for GET %s: %s", path, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="orderbook engine unavailable",
        ) from exc

    if upstream.status_code != 200:
        # Buffer the (typed-envelope) body and pass status + body through
        # verbatim. An unparseable body is the only thing that maps to 502 (as
        # in :func:`_proxy`).
        await upstream.aread()
        await upstream.aclose()
        try:
            payload: Any = upstream.json()
        except ValueError as exc:
            logger.warning("orderbook upstream %d for GET %s", upstream.status_code, path)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="invalid orderbook engine response",
            ) from exc
        return JSONResponse(status_code=upstream.status_code, content=payload)

    async def _iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        _iter(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/health", summary="Order-book engine health (proxied)")
async def orderbook_health(request: Request) -> JSONResponse:
    """Proxy the engine's liveness payload (today's DQ grade included)."""
    return await _proxy(request, "/health")


@router.get("/status", summary="Capture status (proxied)")
async def orderbook_status(request: Request) -> JSONResponse:
    """Proxy the engine's capture status."""
    return await _proxy(request, "/status")


@router.get("/symbols", summary="Captured symbol universe (proxied)")
async def orderbook_symbols(request: Request) -> JSONResponse:
    """Proxy the captured symbol universe."""
    return await _proxy(request, "/symbols")


@router.get("/order-book/{symbol}", summary="L2 order-book snapshot (proxied JSON)")
async def orderbook_order_book(symbol: str, request: Request) -> JSONResponse:
    """Proxy the JSON L2 order-book snapshot for a symbol."""
    return await _proxy(request, f"/order-book/{symbol}")


@router.get(
    "/order-book/{symbol}/stream",
    summary="Order-book update stream (SSE; proxied, unbuffered)",
    response_model=None,
)
async def orderbook_order_book_stream(
    symbol: str, request: Request
) -> StreamingResponse | JSONResponse:
    """Stream order-book SSE updates through unbuffered."""
    return await _proxy_sse(request, f"/order-book/{symbol}/stream")


@router.get("/trades/{symbol}", summary="Time & sales (proxied)")
async def orderbook_trades(symbol: str, request: Request) -> JSONResponse:
    """Proxy the time & sales read for a symbol."""
    return await _proxy(request, f"/trades/{symbol}")


@router.get("/settlements/{series}", summary="Daily settlements for a series (proxied)")
async def orderbook_settlements(series: str, request: Request) -> JSONResponse:
    """Proxy the daily-settlements read for a series."""
    return await _proxy(request, f"/settlements/{series}")


@router.get("/manifest/{date}", summary="Per-day DQ manifest (proxied)")
async def orderbook_manifest(date: str, request: Request) -> JSONResponse:
    """Proxy the per-day data-quality manifest."""
    return await _proxy(request, f"/manifest/{date}")


# NOTE: this literal-path route MUST be declared ABOVE ``GET /greeks/{symbol}``
# — FastAPI matches in declaration order, so a leading path-param route would
# otherwise capture the literal chain read.
@router.get("/greeks", summary="Option-chain greeks (proxied)")
async def orderbook_greeks(request: Request) -> JSONResponse:
    """Proxy the option-chain greeks read."""
    return await _proxy(request, "/greeks")


@router.get("/greeks/{symbol}", summary="Per-symbol greeks (proxied)")
async def orderbook_greeks_symbol(symbol: str, request: Request) -> JSONResponse:
    """Proxy the per-symbol greeks read."""
    return await _proxy(request, f"/greeks/{symbol}")


@router.get("/features/{symbol}", summary="Derived microstructure features (proxied)")
async def orderbook_features(symbol: str, request: Request) -> JSONResponse:
    """Proxy the derived microstructure-features read for a symbol."""
    return await _proxy(request, f"/features/{symbol}")
