"""Tests for ``POST /api/v1/admin/cache/flush``."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from src.db import postgres as pg
from src.db import redis_client as rc
from src.services.errors import CacheError


@pytest.fixture
def patch_lifespan_deps(monkeypatch: pytest.MonkeyPatch, mock_pool: Any) -> None:
    """Mock ``get_pool`` and ``get_redis`` so the lifespan starts cleanly."""

    async def _get_pool() -> Any:
        return mock_pool

    async def _get_redis() -> AsyncMock:
        return AsyncMock()

    monkeypatch.setattr(pg, "get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.ingest.get_pool", _get_pool)
    monkeypatch.setattr("src.main.get_pool", _get_pool)
    monkeypatch.setattr(rc, "get_redis", _get_redis)
    monkeypatch.setattr("src.main.get_redis", _get_redis)


@pytest.fixture
def mock_flush(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace ``flush_all`` in the admin router scope."""
    mock = AsyncMock(return_value=7)
    monkeypatch.setattr("src.api.v1.admin.flush_all", mock)
    return mock


async def test_flush_cache_with_valid_api_key(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    mock_flush: AsyncMock,
) -> None:
    response = await async_client.post(
        "/api/v1/admin/cache/flush",
        headers={"X-API-Key": "test-internal-api-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "flushed"
    assert body["keys_deleted"] == 7
    mock_flush.assert_awaited_once()


async def test_flush_cache_without_api_key(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
) -> None:
    response = await async_client.post("/api/v1/admin/cache/flush")
    assert response.status_code == 403


async def test_flush_cache_wrong_api_key(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    mock_flush: AsyncMock,
) -> None:
    response = await async_client.post(
        "/api/v1/admin/cache/flush",
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 403
    mock_flush.assert_not_awaited()


async def test_flush_cache_when_redis_fails(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = AsyncMock(side_effect=CacheError("redis down"))
    monkeypatch.setattr("src.api.v1.admin.flush_all", mock)
    response = await async_client.post(
        "/api/v1/admin/cache/flush",
        headers={"X-API-Key": "test-internal-api-key"},
    )
    assert response.status_code == 500
    assert "cache flush failed" in response.json()["detail"]
