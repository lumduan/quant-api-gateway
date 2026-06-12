"""``/api/v2/engines/execution/*`` — proxy to the Execution engine.

The standalone ``quant-execution-engine`` (host ``:8400``, in-network
``http://quant-execution-engine:8000``) is the canonical order router and the
sole owner of broker order-routing credentials. The gateway is a **thin
reverse proxy**: it holds no credential, forwards the order surface plus the
caller's ``X-API-Key`` and ``X-Strategy-Id``, and maps transport failures to
clean ``502/503/504``.
Engine 4xx responses — including the typed rejection envelopes (``public_mode``,
``risk_rejected``, ``capability_unsupported``, ``kill_switch_engaged``,
``order_book_unavailable``, ``order_stream_unavailable`` …) — pass through
verbatim. The engine's ``/admin/*`` (kill-switch) surface is deliberately NOT
proxied: owner-mode operations are engine-direct only.

Proxied surface:

* ``GET /health`` — engine liveness (stage + public_mode).
* ``GET /capabilities`` — per-(broker, market) capability matrix.
* ``POST /orders`` — submit a NormalizedOrder (idempotent on client_order_id).
* ``GET /orders/stream`` — **SSE** order-update events.
* ``GET /orders/{client_order_id}`` — read one order's normalized state.
* ``PATCH /orders/{client_order_id}`` — native amend (price/quantity).
* ``DELETE /orders/{client_order_id}`` — cancel a resting order.
* ``GET /order-book/{symbol}`` — JSON order-book snapshot.
* ``GET /order-book/{symbol}/stream`` — **SSE** order-book updates.

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
    strategy_id = request.headers.get("X-Strategy-Id")
    if strategy_id:
        headers["X-Strategy-Id"] = strategy_id
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


async def _proxy_sse(request: Request, path: str) -> StreamingResponse | JSONResponse:
    """Stream an SSE endpoint upstream unbuffered (chunked transfer).

    Unlike :func:`_proxy`, the upstream response body is **not** read into
    memory: chunks flow through as they arrive so order-update / order-book
    events reach the client immediately and a long-idle stream (keep-alive
    comment every ~15 s) stays open. The per-stream read timeout is disabled
    (``read=None``) for exactly that reason; the connect/write timeout still
    applies. A client disconnect cancels the generator, whose ``finally``
    closes the upstream response.

    Non-200 upstream responses (the engine's typed envelopes — 404
    ``order_book_unavailable``, 503 ``order_stream_unavailable``, 401, 422)
    are buffered and returned verbatim as JSON, mirroring :func:`_proxy`.
    """
    client = _get_client()
    settings = get_settings()
    headers: dict[str, str] = {}
    api_key = request.headers.get("X-API-Key")
    if api_key:
        headers["X-API-Key"] = api_key
    strategy_id = request.headers.get("X-Strategy-Id")
    if strategy_id:
        headers["X-Strategy-Id"] = strategy_id
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    # read=None: an idle SSE stream only emits a keep-alive every ~15 s; the
    # default read timeout would kill it. Connect/write timeouts still apply.
    timeout = httpx.Timeout(settings.execution_engine_timeout_seconds, read=None)
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
        logger.warning("execution upstream timeout for GET %s", path)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="execution engine timed out",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("execution upstream unavailable for GET %s: %s", path, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="execution engine unavailable",
        ) from exc

    if upstream.status_code != 200:
        # Buffer the (typed-envelope) body and pass status + body through
        # verbatim — including the engine's 503 ``order_stream_unavailable`` /
        # 404 ``order_book_unavailable`` envelopes. An unparseable body is the
        # only thing that maps to 502 (as in :func:`_proxy`).
        await upstream.aread()
        await upstream.aclose()
        try:
            payload: Any = upstream.json()
        except ValueError as exc:
            logger.warning("execution upstream %d for GET %s", upstream.status_code, path)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="invalid execution engine response",
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


# NOTE: this literal-path route MUST be declared ABOVE
# ``GET /orders/{client_order_id}`` — FastAPI matches in declaration order, so a
# leading path-param route would otherwise capture the literal "stream".
@router.get(
    "/orders/stream",
    summary="Order-update event stream (SSE; proxied, unbuffered)",
    response_model=None,
)
async def execution_orders_stream(request: Request) -> StreamingResponse | JSONResponse:
    """Stream order-update SSE events through unbuffered (Last-Event-ID forwarded)."""
    return await _proxy_sse(request, "/orders/stream")


@router.get("/orders/{client_order_id}", summary="Read one order's normalized state (proxied)")
async def execution_get_order(client_order_id: str, request: Request) -> JSONResponse:
    """Proxy the aggregate order read."""
    return await _proxy(request, "GET", f"/orders/{client_order_id}")


@router.patch(
    "/orders/{client_order_id}",
    summary="Amend a resting order's price/quantity (proxied; native or cancel+replace)",
)
async def execution_amend_order(client_order_id: str, request: Request) -> JSONResponse:
    """Forward the amend body verbatim; typed 4xx envelopes pass through."""
    return await _proxy(request, "PATCH", f"/orders/{client_order_id}")


@router.delete("/orders/{client_order_id}", summary="Cancel a resting order (proxied)")
async def execution_cancel_order(client_order_id: str, request: Request) -> JSONResponse:
    """Proxy the cancel."""
    return await _proxy(request, "DELETE", f"/orders/{client_order_id}")


@router.get("/order-book/{symbol}", summary="Order-book snapshot (proxied JSON)")
async def execution_order_book(symbol: str, request: Request) -> JSONResponse:
    """Proxy the JSON order-book snapshot for a symbol."""
    return await _proxy(request, "GET", f"/order-book/{symbol}")


@router.get(
    "/order-book/{symbol}/stream",
    summary="Order-book update stream (SSE; proxied, unbuffered)",
    response_model=None,
)
async def execution_order_book_stream(
    symbol: str, request: Request
) -> StreamingResponse | JSONResponse:
    """Stream order-book SSE updates through unbuffered (``?market=`` required)."""
    return await _proxy_sse(request, f"/order-book/{symbol}/stream")
