"""Tests for ``GET /api/v1/strategies``."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from src.db import postgres as pg
from src.db import redis_client as rc


@pytest.fixture
def patch_lifespan_deps(monkeypatch: pytest.MonkeyPatch, mock_pool: Any) -> None:
    async def _get_pool() -> Any:
        return mock_pool

    async def _get_redis() -> AsyncMock:
        return AsyncMock()

    monkeypatch.setattr(pg, "get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.ingest.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.performance.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.portfolio.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.strategies.get_pool", _get_pool)
    monkeypatch.setattr("src.main.get_pool", _get_pool)
    monkeypatch.setattr(rc, "get_redis", _get_redis)
    monkeypatch.setattr("src.main.get_redis", _get_redis)


async def test_list_strategies_returns_active_only(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    """The fixture registry has 2 active + 1 inactive entry."""
    response = await async_client.get("/api/v1/strategies")
    assert response.status_code == 200
    body = response.json()
    ids = {entry["id"] for entry in body}
    assert ids == {"csm-set-01", "tfex-01"}
    # Inactive legacy entry must not appear
    assert "legacy-00" not in ids
    # Schema fields are present
    for entry in body:
        assert {"id", "name", "service_url", "capital_weight", "active"} <= entry.keys()


async def test_list_strategies_requires_no_auth(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    """Strategy listing is unauthenticated — only ingestion needs the API key."""
    response = await async_client.get("/api/v1/strategies")
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# GET /strategies/{strategy_id}                                               #
# --------------------------------------------------------------------------- #


async def test_get_strategy_by_id_found(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    response = await async_client.get("/api/v1/strategies/csm-set-01")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "csm-set-01"
    assert body["name"] == "CSM SET Strategy"


async def test_get_strategy_by_id_not_found(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    response = await async_client.get("/api/v1/strategies/nonexistent")
    assert response.status_code == 404
    assert "nonexistent" in response.json()["detail"]


async def test_get_strategy_by_id_inactive_returns_404(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    response = await async_client.get("/api/v1/strategies/legacy-00")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# GET /strategies/{strategy_id}/equity-curve                                  #
# --------------------------------------------------------------------------- #


async def test_get_strategy_equity_curve_found(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    mock_pool: Any,
) -> None:
    row = {
        "strategy_id": "csm-set-01",
        "metadata": (
            '{"equity_curve": [{"date": "2026-05-14", "value": "100"},'
            ' {"date": "2026-05-15", "value": "105"}]}'
        ),
    }
    mock_pool._conn.fetchrow = AsyncMock(return_value=row)

    response = await async_client.get("/api/v1/strategies/csm-set-01/equity-curve")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert body[0]["date"] == "2026-05-14"


async def test_get_strategy_equity_curve_empty(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    mock_pool: Any,
) -> None:
    mock_pool._conn.fetchrow = AsyncMock(return_value=None)

    response = await async_client.get("/api/v1/strategies/csm-set-01/equity-curve")

    assert response.status_code == 200
    assert response.json() == []


async def test_get_strategy_equity_curve_not_found(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    response = await async_client.get("/api/v1/strategies/nonexistent/equity-curve")

    assert response.status_code == 404
    assert "nonexistent" in response.json()["detail"]


async def test_get_strategy_equity_curve_db_failure_returns_500(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    mock_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services.errors import ServiceError

    mock_fetch = AsyncMock(side_effect=ServiceError("connection refused"))
    mock_pool._conn.fetchrow = mock_fetch

    response = await async_client.get("/api/v1/strategies/csm-set-01/equity-curve")

    assert response.status_code == 500
    assert "Failed to query equity curve" in response.json()["detail"]
