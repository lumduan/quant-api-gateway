"""``/api/v2/engines/orderbook/*`` proxy tests (mirrors the execution suite).

The order-book proxy is **GET-only / read-only** — it forwards only the
caller's ``X-API-Key`` (and ``Last-Event-ID`` on streams), never a body or
``X-Strategy-Id``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import src.api.v2.engines.orderbook as ob
from httpx import AsyncClient


class _FakeUpstream:
    """Stand-in for the shared httpx client used by the order-book proxy."""

    def __init__(
        self, *, response: httpx.Response | None = None, exc: Exception | None = None
    ) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        path: str,
        *,
        params: Any = None,
        headers: Any = None,
    ) -> httpx.Response:
        self.calls.append(
            {
                "path": path,
                "params": dict(params or {}),
                "headers": dict(headers or {}),
            }
        )
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


@pytest.fixture(autouse=True)
def _reset_orderbook_client() -> Any:
    """Ensure no real upstream client leaks across order-book proxy tests."""
    ob._client = None
    yield
    ob._client = None


def _patch_upstream(monkeypatch: pytest.MonkeyPatch, fake: _FakeUpstream) -> None:
    monkeypatch.setattr(ob, "_get_client", lambda: fake)


# --------------------------------------------------------------------------- #
# JSON GET proxy — happy path, header/param forwarding, path mapping
# --------------------------------------------------------------------------- #


async def test_orderbook_health_proxied(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /health forwards the engine's liveness payload verbatim."""
    fake = _FakeUpstream(
        response=httpx.Response(
            200,
            json={"status": "ok", "service": "quant-orderbook-engine", "dq_grade": "GREEN"},
        )
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/orderbook/health")
    assert response.status_code == 200
    assert response.json()["dq_grade"] == "GREEN"
    assert fake.calls[0]["path"] == "/health"


async def test_orderbook_status_and_symbols_paths(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, json={}))
    _patch_upstream(monkeypatch, fake)
    await async_client.get("/api/v2/engines/orderbook/status")
    await async_client.get("/api/v2/engines/orderbook/symbols")
    assert fake.calls[0]["path"] == "/status"
    assert fake.calls[1]["path"] == "/symbols"


async def test_orderbook_snapshot_forwards_api_key_and_params(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /order-book/{symbol} forwards X-API-Key + query params; path mapped."""
    fake = _FakeUpstream(
        response=httpx.Response(200, json={"symbol": "S50M26", "bids": [], "asks": []})
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/orderbook/order-book/S50M26",
        params={"depth": "5"},
        headers={"X-API-Key": "k123"},
    )
    assert response.status_code == 200
    assert response.json()["symbol"] == "S50M26"
    call = fake.calls[0]
    assert call["path"] == "/order-book/S50M26"
    assert call["params"] == {"depth": "5"}
    assert call["headers"].get("X-API-Key") == "k123"


async def test_orderbook_does_not_forward_strategy_id(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-only proxy: an X-Strategy-Id header is never forwarded upstream."""
    fake = _FakeUpstream(response=httpx.Response(200, json={}))
    _patch_upstream(monkeypatch, fake)
    await async_client.get(
        "/api/v2/engines/orderbook/symbols", headers={"X-Strategy-Id": "csm-set"}
    )
    assert "X-Strategy-Id" not in fake.calls[0]["headers"]


async def test_orderbook_trades_settlements_manifest_features_paths(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, json={}))
    _patch_upstream(monkeypatch, fake)
    await async_client.get("/api/v2/engines/orderbook/trades/S50M26")
    await async_client.get("/api/v2/engines/orderbook/settlements/S50")
    await async_client.get("/api/v2/engines/orderbook/manifest/2026-06-15")
    await async_client.get("/api/v2/engines/orderbook/features/S50M26")
    assert fake.calls[0]["path"] == "/trades/S50M26"
    assert fake.calls[1]["path"] == "/settlements/S50"
    assert fake.calls[2]["path"] == "/manifest/2026-06-15"
    assert fake.calls[3]["path"] == "/features/S50M26"


async def test_orderbook_greeks_literal_does_not_shadow_symbol(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /greeks (literal) routes to the chain path, NOT /greeks/{symbol}."""
    fake = _FakeUpstream(response=httpx.Response(200, json={}))
    _patch_upstream(monkeypatch, fake)
    await async_client.get("/api/v2/engines/orderbook/greeks")
    await async_client.get("/api/v2/engines/orderbook/greeks/S50M26C800")
    assert fake.calls[0]["path"] == "/greeks"
    assert fake.calls[1]["path"] == "/greeks/S50M26C800"


# --------------------------------------------------------------------------- #
# Error mapping — 4xx passthrough, 5xx→502, timeout→504, connect→503, bad JSON
# --------------------------------------------------------------------------- #


async def test_orderbook_typed_4xx_envelopes_pass_through(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine's 4xx envelopes (404/422) pass through verbatim."""
    for status_code, code in [(404, "symbol_not_found"), (422, "invalid_request")]:
        fake = _FakeUpstream(
            response=httpx.Response(status_code, json={"error": {"code": code, "message": "x"}})
        )
        _patch_upstream(monkeypatch, fake)
        response = await async_client.get("/api/v2/engines/orderbook/order-book/NOPE")
        assert response.status_code == status_code
        assert response.json()["error"]["code"] == code


async def test_orderbook_upstream_5xx_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(500, json={"detail": "boom"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/orderbook/health")
    assert response.status_code == 502


async def test_orderbook_timeout_maps_to_504(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(exc=httpx.TimeoutException("slow"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/orderbook/status")
    assert response.status_code == 504


async def test_orderbook_connect_error_maps_to_503(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(exc=httpx.ConnectError("refused"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/orderbook/symbols")
    assert response.status_code == 503


async def test_orderbook_invalid_json_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, content=b"not json"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/orderbook/health")
    assert response.status_code == 502


async def test_close_orderbook_client() -> None:
    """close_orderbook_client() clears the shared client."""

    class _Closable:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    closable = _Closable()
    ob._client = closable  # type: ignore[assignment]
    await ob.close_orderbook_client()
    assert closable.closed
    assert ob._client is None
    await ob.close_orderbook_client()  # idempotent no-op


async def test_catalog_lists_orderbook(async_client: AsyncClient) -> None:
    """The static fallback catalog includes the order-book engine."""
    response = await async_client.get("/api/v2/engines/catalog")
    assert response.status_code == 200
    slugs = {entry["slug"] for entry in response.json()}
    assert "orderbook" in slugs


# --------------------------------------------------------------------------- #
# SSE pass-through — /order-book/{symbol}/stream
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
    """Build a real AsyncClient over a MockTransport so send(stream=True) streams."""

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    return httpx.AsyncClient(base_url="http://up", transport=httpx.MockTransport(_wrapped))


async def test_orderbook_stream_passes_through_unbuffered(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 SSE upstream streams through as text/event-stream with X-Accel-Buffering."""
    chunks = [b"data: bid\n\n", b": keep-alive\n\n", b"data: ask\n\n"]
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_CapturingStream(chunks),
            headers={"content-type": "text/event-stream"},
        )

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ob, "_get_client", lambda: client)

    body = b""
    async with async_client.stream(
        "GET",
        "/api/v2/engines/orderbook/order-book/S50M26/stream",
        params={"depth": "5"},
        headers={"Last-Event-ID": "42", "X-API-Key": "k123"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-cache"
        async for chunk in response.aiter_raw():
            body += chunk
    assert body == b"".join(chunks)

    # The Last-Event-ID header, X-API-Key, and query params were forwarded upstream.
    req = captured[0]
    assert req.url.path == "/order-book/S50M26/stream"
    assert req.headers.get("last-event-id") == "42"
    assert req.headers.get("x-api-key") == "k123"
    assert dict(req.url.params) == {"depth": "5"}

    await client.aclose()


async def test_orderbook_stream_non200_envelope_passthrough(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-200 SSE upstream (404 envelope) is buffered and passed through as JSON."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "symbol_not_found", "message": "x"}})

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ob, "_get_client", lambda: client)

    response = await async_client.get("/api/v2/engines/orderbook/order-book/NOPE/stream")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "symbol_not_found"
    await client.aclose()


async def test_orderbook_stream_unparseable_non200_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-200 SSE upstream with an unparseable body maps to 502."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"not json")

    client = _streaming_client(handler, captured)
    monkeypatch.setattr(ob, "_get_client", lambda: client)

    response = await async_client.get("/api/v2/engines/orderbook/order-book/S50M26/stream")
    assert response.status_code == 502
    await client.aclose()


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


async def test_orderbook_stream_connect_error_maps_to_503(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connect error on the SSE send maps to 503."""
    monkeypatch.setattr(ob, "_get_client", lambda: _StreamErrorClient(httpx.ConnectError("x")))
    response = await async_client.get("/api/v2/engines/orderbook/order-book/S50M26/stream")
    assert response.status_code == 503


async def test_orderbook_stream_timeout_maps_to_504(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout on the SSE send maps to 504."""
    monkeypatch.setattr(ob, "_get_client", lambda: _StreamErrorClient(httpx.ConnectTimeout("slow")))
    response = await async_client.get("/api/v2/engines/orderbook/order-book/S50M26/stream")
    assert response.status_code == 504
