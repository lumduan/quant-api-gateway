"""``/api/v2/engines/execution/*`` — proxy to the Execution engine.

The standalone ``quant-execution-engine`` (host ``:8400``, in-network
``http://quant-execution-engine:8000``) is the canonical order router and the
sole owner of broker order-routing credentials. The gateway is a **thin
reverse proxy**: it holds no credential, forwards the order surface plus the
caller's ``X-API-Key`` and ``X-Strategy-Id``, and maps transport failures to
clean ``502/503/504``.
Engine **typed-envelope** responses pass through verbatim at the engine's own
status — 4xx *and* 5xx alike (``public_mode`` 403, ``risk_rejected`` 422,
``order_book_unavailable`` 404, ``kill_switch_engaged`` 503,
``broker_circuit_open`` 503, ``order_stream_unavailable`` 503,
``liberator_positions_uncaptured`` 501 …). Only a **bare, envelope-less** 5xx
becomes ``502``.

⚠️ This sentence used to say *"Engine 4xx responses … pass through verbatim"*
while listing two 503s among its own examples — and ``_proxy`` did collapse every
5xx, so both named envelopes were in fact destroyed (TK-0451, fixed 2026-08-27).
The wording is corrected rather than deleted because the wrong version is why
nobody noticed: it described the behaviour everyone wanted.

The engine's ``/admin/*`` (kill-switch) surface is deliberately NOT proxied.

⚠️ **The rule for /admin/* is narrower than it used to be stated, and the
wording is corrected here rather than left to mislead.** It previously read
*"owner-mode operations are engine-direct only"*, which the account reads added
below **falsify** — ``GET /accounts/*`` is owner-mode on the engine and is now
proxied. The line that actually holds is: **the engine's global-safety MUTATIONS
stay engine-direct.** ``/admin/kill-switch*`` changes platform-wide state and
must not be reachable through a public aggregator; a read of venue truth does
not. Enforcement is unchanged either way — the gateway injects **no** credential
of its own and forwards only the caller's ``X-API-Key``, so it grants no
authority the caller did not already hold, and the engine's ``require_api_key``
+ ``require_owner_mode`` still decide.

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
* ``GET /accounts/{account}`` — normalized balance / buying power (``?broker=``).
* ``GET /accounts/{account}/open-orders`` — venue-truth resting orders (``?broker=``).
* ``GET /accounts/{account}/positions`` — venue-truth holdings (``?broker=``).

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


def _is_typed_envelope(payload: Any) -> bool:
    """True when the body is the engine's uniform typed-rejection envelope.

    The engine answers every *deliberate* refusal with
    ``{"error": {"code", "message", "detail"?}}`` (its ``api/error_handlers.py``
    docstring is the contract). An *undeliberate* failure — an unhandled
    exception — comes back as FastAPI's bare ``{"detail": "..."}`` instead. That
    difference is the only reliable signal separating "the engine is telling you
    something" from "the engine fell over", and it is what decides passthrough
    below.

    Checked structurally rather than against a list of known codes on purpose: a
    new engine envelope must not need a matching edit here to survive the hop.
    Whitelisting codes would silently re-introduce TK-0451 for every code added
    after this file was last touched.
    """
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    return isinstance(error, dict) and isinstance(error.get("code"), str)


def _passthrough_or_bad_gateway(
    status_code: int, payload: Any, method: str, path: str
) -> JSONResponse:
    """Decide, for BOTH proxy paths, whether an upstream response survives verbatim.

    🔴 TK-0451. This helper exists because the two proxy functions previously
    disagreed: ``_proxy_sse`` passed the engine's typed 5xx envelopes through
    (its comment names ``order_stream_unavailable`` explicitly) while ``_proxy``
    collapsed **every** ``>= 500`` into a flat ``502 execution engine error``,
    discarding the body. One path was right, the other was wrong, and nothing
    forced them to agree — so the fix is a single decision both call, not two
    parallel edits that can drift apart again.

    The rule:

    * **Typed envelope** — forwarded verbatim at the engine's own status. A
      caller can then distinguish ``kill_switch_engaged`` (503, deliberate halt —
      do NOT retry) from ``broker_circuit_open`` (503, venue trouble) from
      ``streaming_pro_positions_uncaptured`` (501, the venue's element schema
      has never been observed — not a fault, and not retryable until it is).

      ↻ **This example named ``liberator_positions_uncaptured`` and described it
      as "will never work — stop asking" until 2026-09-01. Both halves are now
      false.** The blocker was a missing element-schema capture, and the capture
      was taken 2026-08-28 when real positions were first held: liberator
      positions parse on SET *and* TFEX today. The engine's exception class
      survives but is **never raised**. The streaming_pro code above is the one
      that still fires, and it is the better illustration anyway — it shows a 501
      that is a *deliberate refusal*, which is the distinction this rule exists
      to preserve.
    * **Bare 5xx with no envelope** — ``502``. The engine genuinely failed, and
      502 attributes that to the upstream; forwarding a bare ``500`` would make
      the *gateway* look like the thing that broke.
    * **Anything else** (2xx, and 4xx like ``403 public_mode`` / ``404
      order_not_found``) — verbatim, unchanged from before.
    """
    if status_code >= 500 and not _is_typed_envelope(payload):
        logger.warning("execution upstream %d for %s %s", status_code, method, path)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="execution engine error",
        )
    if status_code >= 500:
        # Deliberate refusal, not a fault — log at INFO so it stops paging as an error.
        logger.info(
            "execution upstream typed %d (%s) for %s %s",
            status_code,
            payload["error"]["code"],
            method,
            path,
        )
    return JSONResponse(status_code=status_code, content=payload)


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

    try:
        payload: Any = upstream.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="invalid execution engine response",
        ) from exc
    # Forward status + body verbatim, INCLUDING the typed 5xx envelopes; only a
    # bare (envelope-less) 5xx becomes 502. See _passthrough_or_bad_gateway.
    return _passthrough_or_bad_gateway(upstream.status_code, payload, method, path)


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
        # Buffer the body and route it through the SAME decision the JSON proxy
        # uses, so the two paths cannot drift again (TK-0451 was exactly that
        # drift). Typed envelopes — the 503 ``order_stream_unavailable`` / 404
        # ``order_book_unavailable`` this path always handled correctly — still
        # pass through verbatim; an unparseable body still maps to 502.
        #
        # ⚠️ ONE BEHAVIOUR CHANGE HERE, called out rather than slipped in: a bare
        # envelope-less 5xx used to pass through with its own status and now
        # becomes 502, matching the JSON path. A caller cannot tell an engine
        # crash from a gateway crash otherwise.
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
        return _passthrough_or_bad_gateway(upstream.status_code, payload, "GET", path)

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


# NOTE: unlike ``/orders/stream`` above, the declaration order of the two
# ``/accounts`` routes is NOT load-bearing — they differ in SEGMENT COUNT, and a
# path parameter never matches across a ``/``. Said explicitly because the
# neighbouring NOTE documents a case where order IS required, and copying that
# reasoning here would assert a constraint that does not exist.
@router.get(
    "/accounts/{account}/open-orders",
    summary="Venue-truth resting orders for one account (proxied; ``?broker=`` required)",
)
async def execution_account_open_orders(account: str, request: Request) -> JSONResponse:
    """Proxy the venue-truth open-orders read.

    This is the VENUE's view, not the engine's durable store — the two can
    legitimately disagree while a submit is in flight, which is the whole reason
    the endpoint exists. The engine gates it owner-mode; the gateway does not
    relax that (see the module docstring).
    """
    return await _proxy(request, "GET", f"/accounts/{account}/open-orders")


@router.get(
    "/accounts/{account}/positions",
    summary="Venue-truth holdings for one account (proxied; ``?broker=`` required)",
)
async def execution_account_positions(account: str, request: Request) -> JSONResponse:
    """Proxy the venue-truth positions read.

    🔴 **Two engine guarantees have to survive this hop, and a "helpful" proxy
    would destroy both.**

    **An empty list means the account holds nothing** — and it can only mean that
    because every engine path unable to answer *raises* instead. A proxy that
    caught an upstream failure and substituted ``[]`` would report a holding
    account as flat, and *flat* is a plausible answer a caller will act on. This
    proxy interprets nothing: ``_proxy`` forwards status and payload verbatim.

    **A 501 is a refusal, not a fault.** ``streaming_pro_positions_uncaptured``
    means the venue's element schema has never been observed, so the engine
    refuses rather than inventing field names. It is a typed envelope, so
    :func:`_passthrough_or_bad_gateway` forwards it intact rather than flattening
    it to a bare 502 — the TK-0451 defect, fixed 2026-08-27 and pinned by tests
    here.

    ⚠️ Coverage is asymmetric by venue, not by omission: ``liberator`` parses SET
    and TFEX; ``streaming_pro`` parses SET, answers ``[]`` for a *flat* TFEX
    account, and 501s for one that holds something.

    ``side: null`` means *the venue did not distinguish* — never *flat*, never
    *long*. SET equities cannot be short and neither venue sends a side for them.
    """
    return await _proxy(request, "GET", f"/accounts/{account}/positions")


@router.get(
    "/accounts/{account}",
    summary="Normalized account balance / buying power (proxied; ``?broker=`` required)",
)
async def execution_account(account: str, request: Request) -> JSONResponse:
    """Proxy the normalized balance read.

    🔴 The engine RAISES for an account it cannot read rather than returning a
    zero, and that distinction must survive the hop: a coerced ``0`` here would
    report a confident balance for an unreadable account. Nothing in this proxy
    interprets the body — ``_proxy`` forwards status and payload verbatim — so a
    ``null`` field (the broker did not report it) stays distinct from ``0`` (the
    broker reported zero).
    """
    return await _proxy(request, "GET", f"/accounts/{account}")
