"""``/api/v2/engines/execution/*`` proxy tests (mirrors the market-data suite)."""

from __future__ import annotations

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


async def test_execution_upstream_5xx_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
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
