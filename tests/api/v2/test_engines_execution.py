"""``/api/v2/engines/execution/*`` proxy tests (mirrors the market-data suite)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import src.api.v2.engines.execution as ex
from httpx import AsyncClient


class _FakeUpstream:
    """Stand-in for the shared httpx client used by the execution proxy."""

    def __init__(
        self, *, response: httpx.Response | None = None, exc: Exception | None = None
    ) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        headers: Any = None,
        content: Any = None,
    ) -> httpx.Response:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": dict(params or {}),
                "headers": dict(headers or {}),
                "content": content,
            }
        )
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


@pytest.fixture(autouse=True)
def _reset_execution_client() -> Any:
    """Ensure no real upstream client leaks across execution proxy tests."""
    ex._client = None
    yield
    ex._client = None


def _patch_upstream(monkeypatch: pytest.MonkeyPatch, fake: _FakeUpstream) -> None:
    monkeypatch.setattr(ex, "_get_client", lambda: fake)


async def test_execution_health_proxied(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /health forwards the engine's liveness payload (stage included)."""
    fake = _FakeUpstream(
        response=httpx.Response(
            200,
            json={
                "status": "ok",
                "service": "quant-execution-engine",
                "stage": "sim",
                "public_mode": True,
            },
        )
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/health")
    assert response.status_code == 200
    assert response.json()["stage"] == "sim"
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["path"] == "/health"


async def test_execution_capabilities_proxied(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, json={"stage": "sim", "capabilities": []}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/capabilities")
    assert response.status_code == 200
    assert fake.calls[0]["path"] == "/capabilities"


async def test_execution_post_orders_forwards_body_and_api_key(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /orders forwards the raw JSON body + X-API-Key; 201 passes through."""
    upstream_body = {"client_order_id": "abc", "status": "FILLED"}
    fake = _FakeUpstream(response=httpx.Response(201, json=upstream_body))
    _patch_upstream(monkeypatch, fake)
    order = {"client_order_id": "abc", "broker": "sim", "quantity": 1}
    response = await async_client.post(
        "/api/v2/engines/execution/orders",
        json=order,
        headers={"X-API-Key": "k123"},
    )
    assert response.status_code == 201
    assert response.json() == upstream_body
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/orders"
    assert call["headers"].get("X-API-Key") == "k123"
    assert call["headers"].get("Content-Type", "").startswith("application/json")
    assert b'"client_order_id"' in call["content"]


async def test_execution_post_orders_forwards_strategy_id(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /orders forwards X-Strategy-Id so the engine can persist strategy_id."""
    fake = _FakeUpstream(response=httpx.Response(201, json={"status": "FILLED"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.post(
        "/api/v2/engines/execution/orders",
        json={"client_order_id": "abc"},
        headers={"X-Strategy-Id": "csm-set"},
    )
    assert response.status_code == 201
    assert fake.calls[0]["headers"].get("X-Strategy-Id") == "csm-set"


async def test_execution_post_orders_without_strategy_id_omits_header(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A POST without X-Strategy-Id must not synthesise the header upstream."""
    fake = _FakeUpstream(response=httpx.Response(201, json={"status": "FILLED"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.post(
        "/api/v2/engines/execution/orders", json={"client_order_id": "abc"}
    )
    assert response.status_code == 201
    assert "X-Strategy-Id" not in fake.calls[0]["headers"]


async def test_execution_resend_200_passthrough(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An idempotent resend's 200 (vs 201) passes through verbatim."""
    fake = _FakeUpstream(response=httpx.Response(200, json={"status": "FILLED"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.post("/api/v2/engines/execution/orders", json={})
    assert response.status_code == 200


async def test_execution_get_and_delete_order_forward_path(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, json={"status": "CANCELLED"}))
    _patch_upstream(monkeypatch, fake)
    await async_client.get("/api/v2/engines/execution/orders/oid-1")
    await async_client.delete("/api/v2/engines/execution/orders/oid-1")
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["path"] == "/orders/oid-1"
    assert fake.calls[1]["method"] == "DELETE"
    assert fake.calls[1]["path"] == "/orders/oid-1"


async def test_execution_typed_4xx_envelopes_pass_through(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine's typed rejection envelopes (403/422/429/503…) pass verbatim."""
    for status_code, code in [
        (403, "public_mode"),
        (422, "risk_rejected"),
        (429, "risk_rejected"),
        (404, "order_not_found"),
        (409, "illegal_transition"),
    ]:
        fake = _FakeUpstream(
            response=httpx.Response(status_code, json={"error": {"code": code, "message": "x"}})
        )
        _patch_upstream(monkeypatch, fake)
        response = await async_client.post("/api/v2/engines/execution/orders", json={})
        assert response.status_code == status_code
        assert response.json()["error"]["code"] == code


async def test_execution_upstream_BARE_5xx_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renamed 2026-08-27 (TK-0451): it was `..._upstream_5xx_maps_to_502`.

    The old name asserted a fact that is no longer true in general — a TYPED 5xx
    envelope now passes through verbatim. This case still holds and still matters,
    but only because its body (`{"detail": "boom"}`) carries no envelope. The test
    never changed; the claim its NAME made did, and a name is read far more often
    than a body.
    """
    fake = _FakeUpstream(response=httpx.Response(500, json={"detail": "boom"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/health")
    assert response.status_code == 502


async def test_execution_timeout_maps_to_504(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(exc=httpx.TimeoutException("slow"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.post("/api/v2/engines/execution/orders", json={})
    assert response.status_code == 504


async def test_execution_connect_error_maps_to_503(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(exc=httpx.ConnectError("refused"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/capabilities")
    assert response.status_code == 503


async def test_execution_invalid_json_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, content=b"not json"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/health")
    assert response.status_code == 502


async def test_execution_query_params_forwarded(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, json={}))
    _patch_upstream(monkeypatch, fake)
    await async_client.get("/api/v2/engines/execution/orders/oid-1", params={"verbose": "1"})
    assert fake.calls[0]["params"] == {"verbose": "1"}


async def test_close_execution_client() -> None:
    """close_execution_client() clears the shared client."""

    class _Closable:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    closable = _Closable()
    ex._client = closable  # type: ignore[assignment]
    await ex.close_execution_client()
    assert closable.closed
    assert ex._client is None
    await ex.close_execution_client()  # idempotent no-op


async def test_catalog_lists_execution(async_client: AsyncClient) -> None:
    """The static fallback catalog includes the execution engine."""
    response = await async_client.get("/api/v2/engines/catalog")
    assert response.status_code == 200
    slugs = {entry["slug"] for entry in response.json()}
    assert "execution" in slugs


# --------------------------------------------------------------------------- #
# PATCH /orders/{cid} — native amend (buffered proxy)
# --------------------------------------------------------------------------- #


async def test_execution_patch_order_forwards_body_and_api_key(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATCH /orders/{cid} forwards method+path+body+X-API-Key; 200 passes through."""
    upstream_body = {"client_order_id": "oid-1", "status": "PENDING_REPLACE"}
    fake = _FakeUpstream(response=httpx.Response(200, json=upstream_body))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.patch(
        "/api/v2/engines/execution/orders/oid-1",
        json={"price": "10.50", "quantity": 200},
        headers={"X-API-Key": "k123"},
    )
    assert response.status_code == 200
    assert response.json() == upstream_body
    call = fake.calls[0]
    assert call["method"] == "PATCH"
    assert call["path"] == "/orders/oid-1"
    assert call["headers"].get("X-API-Key") == "k123"
    assert b'"price"' in call["content"]


async def test_execution_patch_order_409_envelope_passthrough(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typed 409 amend-reject envelope passes through verbatim."""
    fake = _FakeUpstream(
        response=httpx.Response(409, json={"error": {"code": "illegal_transition", "message": "x"}})
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.patch(
        "/api/v2/engines/execution/orders/oid-1", json={"price": "1"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "illegal_transition"


# --------------------------------------------------------------------------- #
# SSE pass-through — /orders/stream and /order-book/{symbol}/stream
# --------------------------------------------------------------------------- #


class _CapturingStream(httpx.AsyncByteStream):
    """An async byte stream that records whether it was closed (aiter once)."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> Any:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _streaming_client(
    handler: Callable[[httpx.Request], httpx.Response], captured: list[httpx.Request]
) -> httpx.AsyncClient:
    """Build a real AsyncClient over a MockTransport so send(stream=True) streams.

    ``handler(request) -> httpx.Response`` decides the upstream response; every
    request is appended to ``captured`` so tests can assert what the gateway
    forwarded (path, query params, headers).
    """

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    return httpx.AsyncClient(base_url="http://up", transport=httpx.MockTransport(_wrapped))


async def test_execution_orders_stream_passes_through_unbuffered(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 SSE upstream streams through as text/event-stream with X-Accel-Buffering."""
    chunks = [b"data: a\n\n", b": keep-alive\n\n", b"data: b\n\n"]
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_CapturingStream(chunks),
            headers={"content-type": "text/event-stream"},
        )

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ex, "_get_client", lambda: client)

    body = b""
    async with async_client.stream(
        "GET",
        "/api/v2/engines/execution/orders/stream",
        params={
            "strategy_id": "s1",
            "client_order_id": "oid-1",
            "last_event_id": "7",
        },
        headers={"Last-Event-ID": "42", "X-API-Key": "k123"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-cache"
        async for chunk in response.aiter_raw():
            body += chunk
    assert body == b"".join(chunks)

    # The Last-Event-ID header and all query params were forwarded upstream.
    req = captured[0]
    assert req.url.path == "/orders/stream"
    assert req.headers.get("last-event-id") == "42"
    assert req.headers.get("x-api-key") == "k123"
    params = dict(req.url.params)
    assert params["strategy_id"] == "s1"
    assert params["client_order_id"] == "oid-1"
    assert params["last_event_id"] == "7"

    await client.aclose()


async def test_execution_orders_stream_forwards_strategy_id(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SSE stream forwards X-Strategy-Id upstream for restart-safe filtering."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_CapturingStream([b"data: x\n\n"]),
            headers={"content-type": "text/event-stream"},
        )

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ex, "_get_client", lambda: client)

    async with async_client.stream(
        "GET",
        "/api/v2/engines/execution/orders/stream",
        headers={"X-Strategy-Id": "s1"},
    ) as response:
        async for _ in response.aiter_raw():
            pass

    assert captured[0].headers.get("x-strategy-id") == "s1"
    await client.aclose()


async def test_execution_orders_stream_does_not_shadow_get_order(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /orders/stream hits the SSE route, NOT the /orders/{cid} path param."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_CapturingStream([b"data: x\n\n"]),
            headers={"content-type": "text/event-stream"},
        )

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ex, "_get_client", lambda: client)

    async with async_client.stream("GET", "/api/v2/engines/execution/orders/stream") as response:
        async for _ in response.aiter_raw():
            pass

    # The upstream saw the literal stream path, not /orders/<captured-cid>.
    assert captured[0].url.path == "/orders/stream"
    await client.aclose()


async def test_execution_orders_stream_503_envelope_passthrough(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-200 SSE upstream (503 order_stream_unavailable) passes through as JSON."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json={"error": {"code": "order_stream_unavailable", "message": "x"}}
        )

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ex, "_get_client", lambda: client)

    response = await async_client.get("/api/v2/engines/execution/orders/stream")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "order_stream_unavailable"
    await client.aclose()


async def test_execution_orders_stream_unparseable_non200_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-200 SSE upstream with an unparseable body maps to 502."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"not json")

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ex, "_get_client", lambda: client)

    response = await async_client.get("/api/v2/engines/execution/orders/stream")
    assert response.status_code == 502
    await client.aclose()


async def test_execution_orders_stream_connect_error_maps_to_503(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connect error on the SSE send maps to 503."""
    # The SSE helper uses build_request/send; use a stub that raises on send.
    monkeypatch.setattr(ex, "_get_client", lambda: _StreamErrorClient(httpx.ConnectError("x")))
    response = await async_client.get("/api/v2/engines/execution/orders/stream")
    assert response.status_code == 503


async def test_execution_orders_stream_timeout_maps_to_504(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout on the SSE send maps to 504."""
    monkeypatch.setattr(ex, "_get_client", lambda: _StreamErrorClient(httpx.ConnectTimeout("slow")))
    response = await async_client.get("/api/v2/engines/execution/orders/stream")
    assert response.status_code == 504


class _StreamErrorClient:
    """A client whose build_request works but send() always raises (SSE error path)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def build_request(
        self,
        method: str,
        url: str,
        *,
        params: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> httpx.Request:
        return httpx.Request(method, f"http://up{url}", params=params, headers=headers)

    async def send(self, *args: Any, **kwargs: Any) -> httpx.Response:
        raise self._exc


# --------------------------------------------------------------------------- #
# Order-book snapshot (plain JSON proxy) and stream
# --------------------------------------------------------------------------- #


async def test_execution_order_book_snapshot_json_proxy(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /order-book/{symbol} is a plain JSON proxy; 200 passes through."""
    fake = _FakeUpstream(
        response=httpx.Response(200, json={"symbol": "PTT", "bids": [], "asks": []})
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/execution/order-book/PTT", params={"market": "SET"}
    )
    assert response.status_code == 200
    assert response.json()["symbol"] == "PTT"
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["path"] == "/order-book/PTT"
    assert fake.calls[0]["params"] == {"market": "SET"}


async def test_execution_order_book_snapshot_404_envelope(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 order_book_unavailable envelope passes through verbatim."""
    fake = _FakeUpstream(
        response=httpx.Response(
            404, json={"error": {"code": "order_book_unavailable", "message": "x"}}
        )
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/order-book/PTT")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "order_book_unavailable"


async def test_execution_order_book_stream_passes_through(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /order-book/{symbol}/stream streams SSE through unbuffered."""
    chunks = [b"data: bid\n\n", b"data: ask\n\n"]
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_CapturingStream(chunks),
            headers={"content-type": "text/event-stream"},
        )

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ex, "_get_client", lambda: client)

    body = b""
    async with async_client.stream(
        "GET",
        "/api/v2/engines/execution/order-book/PTT/stream",
        params={"market": "SET"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        async for chunk in response.aiter_raw():
            body += chunk
    assert body == b"".join(chunks)
    assert captured[0].url.path == "/order-book/PTT/stream"
    assert dict(captured[0].url.params) == {"market": "SET"}
    await client.aclose()


# ---------------------------------------- TK-0451: typed 5xx envelopes must survive the hop


# The engine's uniform envelope, verbatim in shape from its api/error_handlers.py
# contract and confirmed against a live 409 on the AWS node 2026-08-27:
#   {"error": {"code": ..., "message": ..., "detail": {...}}}
def _envelope(code: str, message: str = "x") -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


@pytest.mark.parametrize(
    ("code", "upstream_status"),
    [
        ("kill_switch_engaged", 503),
        ("broker_circuit_open", 503),
        ("order_stream_unavailable", 503),
        ("liberator_positions_uncaptured", 501),
    ],
)
async def test_a_typed_5xx_envelope_passes_through_verbatim(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    upstream_status: int,
) -> None:
    """🔴 TK-0451. These four were being flattened into `502 execution engine error`.

    All four are parametrised rather than tested as one, because they are the
    complete set of 5xx codes in the engine's map and each carries a DIFFERENT
    correct caller response — retrying a kill-switch halt is wrong, retrying a
    501 is futile, retrying a tripped circuit is merely premature. A single
    representative case would have proven the mechanism without proving the set.
    """
    fake = _FakeUpstream(response=httpx.Response(upstream_status, json=_envelope(code)))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/capabilities")
    assert response.status_code == upstream_status
    assert response.json()["error"]["code"] == code


async def test_a_BARE_5xx_with_no_envelope_still_becomes_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the fix, and the half a passthrough-everything patch loses.

    An unhandled engine exception is FastAPI's bare ``{"detail": ...}`` — no
    ``error.code``. That is a genuine fault, not a message, and it must stay 502:
    forwarding a bare 500 would make the GATEWAY look like the thing that broke.
    """
    fake = _FakeUpstream(response=httpx.Response(500, json={"detail": "Internal Server Error"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/capabilities")
    assert response.status_code == 502
    assert response.json()["detail"] == "execution engine error"


async def test_an_envelope_SHAPED_body_that_is_not_one_does_not_get_passthrough(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classifier is structural, so its edges are worth pinning.

    ``{"error": "something"}`` (a STRING, not the envelope object) is what a
    naive `"error" in payload` check would wave through. It is not an engine
    envelope and must not buy a 5xx passthrough.
    """
    fake = _FakeUpstream(response=httpx.Response(503, json={"error": "just a string"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/capabilities")
    assert response.status_code == 502


async def test_an_UNKNOWN_future_envelope_code_survives_without_a_gateway_edit(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why the classifier is structural instead of a whitelist of known codes.

    A code-list would silently re-introduce TK-0451 for every envelope the engine
    adds after this file was last touched — and the failure would be invisible,
    because the gateway would answer 502 exactly as it always had.
    """
    fake = _FakeUpstream(
        response=httpx.Response(503, json=_envelope("some_code_invented_next_year"))
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/capabilities")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "some_code_invented_next_year"


async def test_4xx_envelopes_are_unaffected_by_the_5xx_fix(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: the 4xx path already worked and must not have moved."""
    fake = _FakeUpstream(response=httpx.Response(403, json=_envelope("public_mode")))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/execution/capabilities")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "public_mode"


async def test_the_SSE_path_makes_the_SAME_decision_as_the_json_path(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 The behaviour change in this fix, pinned rather than slipped in.

    `_proxy_sse` used to forward ANY non-200 verbatim, so a bare envelope-less
    5xx kept its own status; it now maps to 502 like the JSON path. Both call one
    helper, so the two cannot drift again — which is what TK-0451 actually was:
    not a wrong rule, but two rules where there should have been one.

    Its sibling `test_execution_orders_stream_503_envelope_passthrough` covers
    the other half (a typed 503 still passes through), and that test predates this
    fix — evidence the SSE path was the one that had it right.
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "Internal Server Error"})

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ex, "_get_client", lambda: client)

    response = await async_client.get("/api/v2/engines/execution/orders/stream")
    assert response.status_code == 502
    assert response.json()["detail"] == "execution engine error"
    await client.aclose()


# ------------------------------------------- account reads (balance / open-orders)


async def test_execution_account_balance_proxied_with_broker_query(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /accounts/{account} forwards the path, ?broker=, and X-API-Key."""
    body = {
        "account": "0532099",
        "account_type": "derivative",
        "buying_power": "10567.77",
        "equity": "10567.77",
    }
    fake = _FakeUpstream(response=httpx.Response(200, json=body))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/execution/accounts/0532099",
        params={"broker": "streaming_pro"},
        headers={"X-API-Key": "k123"},
    )
    assert response.status_code == 200
    assert response.json() == body
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/accounts/0532099"
    assert call["params"] == {"broker": "streaming_pro"}
    assert call["headers"]["X-API-Key"] == "k123"


async def test_execution_account_open_orders_proxied(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open-orders sub-path is NOT swallowed by the balance route."""
    fake = _FakeUpstream(response=httpx.Response(200, json={"orders": []}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/execution/accounts/70173297/open-orders",
        params={"broker": "liberator"},
    )
    assert response.status_code == 200
    # The whole point: the extra segment must reach the engine, not be dropped.
    assert fake.calls[0]["path"] == "/accounts/70173297/open-orders"


async def test_the_gateway_GRANTS_NO_AUTHORITY_a_401_passes_through_verbatim(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 The security property of this proxy, asserted rather than assumed.

    The account reads are OWNER-MODE on the engine. The gateway must be a pure
    hop: it holds no credential, injects none, and must not convert the engine's
    rejection into anything else. If a future edit made the gateway supply its
    own key, this test goes red — which is the only thing standing between "thin
    proxy" and "privilege escalation via aggregator".
    """
    fake = _FakeUpstream(
        response=httpx.Response(401, json={"detail": "invalid or missing X-API-Key"})
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/execution/accounts/0532099", params={"broker": "streaming_pro"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid or missing X-API-Key"
    # No key was sent, so none may be forwarded — and none may be invented.
    assert "X-API-Key" not in fake.calls[0]["headers"]


async def test_an_UNREADABLE_account_error_is_not_softened_into_a_zero(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine raises rather than reporting 0 for an account it cannot read.

    That refusal has to survive the hop. A proxy that caught the error and
    substituted a default would report a confident zero balance for an account
    nobody could read — the exact defect the engine-side fix removed.

    The engine maps ``streaming_pro_account_unavailable`` to **404** (verified in
    ``api/error_handlers.py``, not assumed).

    ⚠️ This docstring used to add *"see the 5xx test below for the envelopes that
    do NOT"* pass through — true when written, and **false since TK-0451 was fixed
    on 2026-08-27**: typed 5xx envelopes now pass through too. The companion test
    that pinned that defect was deleted in the same rebase rather than left to
    fail, since the behaviour it asserted no longer exists.
    """
    fake = _FakeUpstream(
        response=httpx.Response(
            404,
            json={
                "error": "streaming_pro_account_unavailable",
                "detail": "neither front answered",
            },
        )
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/execution/accounts/9999999", params={"broker": "streaming_pro"}
    )
    assert response.status_code == 404
    assert response.json()["error"] == "streaming_pro_account_unavailable"


async def test_a_null_field_stays_null_and_is_never_coerced_to_zero(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """null (broker did not report) and 0 (broker reported zero) are different facts.

    Liberator's CASH entry omits the margin block while its DERIVATIVE entry
    reports it as 0. Both readings cross this proxy in the same response shape,
    so a coercion in either direction would corrupt a margin calculation.
    """
    fake = _FakeUpstream(
        response=httpx.Response(
            200,
            json={
                "account": "70173292",
                "buying_power": "50885.83",
                "equity": None,
                "initial_margin": None,
                "maintenance_margin": 0,
            },
        )
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/execution/accounts/70173292", params={"broker": "liberator"}
    )
    payload = response.json()
    assert payload["equity"] is None
    assert payload["initial_margin"] is None
    assert payload["maintenance_margin"] == 0
