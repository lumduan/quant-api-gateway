"""``/api/v2/engines/crypto/*`` proxy tests (mirrors the order-book suite).

The crypto proxy is **GET-only / read-only** — it forwards only the caller's
``X-API-Key``, never a body or ``X-Strategy-Id``. The crypto engine has no SSE /
``/order-book`` (Phase 3+), so this proxy is JSON-only.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import src.api.v2.engines.crypto as cx
from httpx import AsyncClient


class _FakeUpstream:
    """Stand-in for the shared httpx client used by the crypto proxy."""

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
def _reset_crypto_client() -> Any:
    """Ensure no real upstream client leaks across crypto proxy tests."""
    cx._client = None
    yield
    cx._client = None


def _patch_upstream(monkeypatch: pytest.MonkeyPatch, fake: _FakeUpstream) -> None:
    monkeypatch.setattr(cx, "_get_client", lambda: fake)


# --------------------------------------------------------------------------- #
# JSON GET proxy — happy path, header/param forwarding, path mapping
# --------------------------------------------------------------------------- #


async def test_crypto_health_proxied(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /health forwards the engine's liveness payload verbatim."""
    fake = _FakeUpstream(
        response=httpx.Response(
            200,
            json={"status": "ok", "service": "quant-crypto-engine", "today_grade": "AMBER"},
        )
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/crypto/health")
    assert response.status_code == 200
    assert response.json()["today_grade"] == "AMBER"
    assert fake.calls[0]["path"] == "/health"


async def test_crypto_status_and_symbols_paths(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, json={}))
    _patch_upstream(monkeypatch, fake)
    await async_client.get("/api/v2/engines/crypto/status")
    await async_client.get("/api/v2/engines/crypto/symbols")
    assert fake.calls[0]["path"] == "/status"
    assert fake.calls[1]["path"] == "/symbols"


async def test_crypto_trades_forwards_api_key_params_and_slash_symbol(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /trades/{symbol:path} forwards X-API-Key + params; a slash symbol maps verbatim."""
    fake = _FakeUpstream(
        response=httpx.Response(
            200, json={"symbol": "BTC/USDT", "venue": "binance_th", "trades": []}
        )
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/crypto/trades/BTC/USDT",
        params={"venue": "binance_th", "limit": "50"},
        headers={"X-API-Key": "k123"},
    )
    assert response.status_code == 200
    assert response.json()["symbol"] == "BTC/USDT"
    call = fake.calls[0]
    assert call["path"] == "/trades/BTC/USDT"
    assert call["params"] == {"venue": "binance_th", "limit": "50"}
    assert call["headers"].get("X-API-Key") == "k123"


async def test_crypto_manifest_path_and_source_param(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(
        response=httpx.Response(200, json={"date": "2026-07-07", "grade": "AMBER"})
    )
    _patch_upstream(monkeypatch, fake)
    await async_client.get(
        "/api/v2/engines/crypto/manifest/2026-07-07", params={"source": "bitkub"}
    )
    call = fake.calls[0]
    assert call["path"] == "/manifest/2026-07-07"
    assert call["params"] == {"source": "bitkub"}


async def test_crypto_does_not_forward_strategy_id(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-only proxy: an X-Strategy-Id header is never forwarded upstream."""
    fake = _FakeUpstream(response=httpx.Response(200, json={}))
    _patch_upstream(monkeypatch, fake)
    await async_client.get("/api/v2/engines/crypto/symbols", headers={"X-Strategy-Id": "csm-set"})
    assert "X-Strategy-Id" not in fake.calls[0]["headers"]


# --------------------------------------------------------------------------- #
# Error mapping — 4xx passthrough, 5xx→502, timeout→504, connect→503, bad JSON
# --------------------------------------------------------------------------- #


async def test_crypto_typed_4xx_envelopes_pass_through(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine's 4xx envelopes (404/422) pass through verbatim."""
    for status_code, detail in [(404, "no DQ manifest"), (422, "day must be an ISO date")]:
        fake = _FakeUpstream(response=httpx.Response(status_code, json={"detail": detail}))
        _patch_upstream(monkeypatch, fake)
        response = await async_client.get("/api/v2/engines/crypto/manifest/nope")
        assert response.status_code == status_code
        assert response.json()["detail"] == detail


async def test_crypto_upstream_5xx_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(500, json={"detail": "boom"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/crypto/health")
    assert response.status_code == 502


async def test_crypto_timeout_maps_to_504(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(exc=httpx.TimeoutException("slow"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/crypto/status")
    assert response.status_code == 504


async def test_crypto_connect_error_maps_to_503(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(exc=httpx.ConnectError("refused"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/crypto/symbols")
    assert response.status_code == 503


async def test_crypto_invalid_json_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, content=b"not json"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/crypto/health")
    assert response.status_code == 502


# --------------------------------------------------------------------------- #
# Client lifecycle + catalog
# --------------------------------------------------------------------------- #


async def test_get_client_builds_and_caches() -> None:
    """_get_client() lazily builds one shared client and reuses it."""
    client = cx._get_client()
    assert isinstance(client, httpx.AsyncClient)
    assert cx._get_client() is client  # cached (no rebuild)
    await cx.close_crypto_client()
    assert cx._client is None


async def test_close_crypto_client() -> None:
    """close_crypto_client() clears the shared client (idempotent)."""

    class _Closable:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    closable = _Closable()
    cx._client = closable  # type: ignore[assignment]
    await cx.close_crypto_client()
    assert closable.closed
    assert cx._client is None
    await cx.close_crypto_client()  # idempotent no-op


async def test_catalog_lists_crypto(async_client: AsyncClient) -> None:
    """The static fallback catalog includes the crypto engine."""
    response = await async_client.get("/api/v2/engines/catalog")
    assert response.status_code == 200
    slugs = {entry["slug"] for entry in response.json()}
    assert "crypto" in slugs
