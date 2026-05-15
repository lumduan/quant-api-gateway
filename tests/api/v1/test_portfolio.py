"""Tests for ``GET /api/v1/portfolio/*`` endpoints."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from src.db import postgres as pg
from src.db import redis_client as rc
from src.schemas.gateway import PortfolioSnapshotResponse
from src.schemas.registry import StrategyRegistry
from src.services.errors import CacheError


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
    monkeypatch.setattr("src.main.get_pool", _get_pool)
    monkeypatch.setattr(rc, "get_redis", _get_redis)
    monkeypatch.setattr("src.main.get_redis", _get_redis)


def _make_snapshot_row(
    total_portfolio: float = 100000.0,
    weighted_return: float = 0.01,
    combined_drawdown: float | None = -0.05,
    active_strategies: int = 1,
    time: datetime | None = None,
) -> dict[str, Any]:
    t = time or datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC)
    return {
        "time": t,
        "total_portfolio": total_portfolio,
        "weighted_return": weighted_return,
        "combined_drawdown": combined_drawdown,
        "active_strategies": active_strategies,
        "allocation": '{"csm-set-01": 1.0}',
    }


def _make_cached_snapshot_response() -> PortfolioSnapshotResponse:
    return PortfolioSnapshotResponse(
        snapshot_date=date(2026, 5, 15),
        total_portfolio_value=Decimal("100000"),
        weighted_daily_return=Decimal("0.010000"),
        combined_drawdown=Decimal("-0.0500"),
        active_strategies=1,
        allocation={"csm-set-01": Decimal("1.0")},
        computed_at=datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
    )


# --------------------------------------------------------------------------- #
# Latest Snapshot                                                             #
# --------------------------------------------------------------------------- #


async def test_latest_snapshot_cache_hit(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    cached = _make_cached_snapshot_response()
    mock_get = AsyncMock(return_value=cached)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.portfolio.set_cached", mock_set)

    response = await async_client.get("/api/v1/portfolio/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["active_strategies"] == 1
    assert float(body["total_portfolio_value"]) == 100000.0
    mock_get.assert_awaited_once()
    mock_set.assert_not_awaited()
    mock_pool._conn.fetchrow.assert_not_awaited()


async def test_latest_snapshot_cache_miss(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.portfolio.set_cached", mock_set)

    row = _make_snapshot_row()
    mock_pool._conn.fetchrow = AsyncMock(return_value=row)

    response = await async_client.get("/api/v1/portfolio/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["active_strategies"] == 1
    mock_set.assert_awaited_once()


async def test_latest_snapshot_empty_table_returns_404(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)

    mock_pool._conn.fetchrow = AsyncMock(return_value=None)

    response = await async_client.get("/api/v1/portfolio/snapshot")

    assert response.status_code == 404
    assert "No portfolio snapshots" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Snapshot by Date                                                            #
# --------------------------------------------------------------------------- #


async def test_snapshot_by_date_found(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)
    monkeypatch.setattr("src.api.v1.portfolio.set_cached", AsyncMock())

    row = _make_snapshot_row()
    mock_pool._conn.fetchrow = AsyncMock(return_value=row)

    response = await async_client.get("/api/v1/portfolio/snapshot/2026-05-15")

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_date"] == "2026-05-15"


async def test_snapshot_by_date_not_found_returns_404(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)
    mock_pool._conn.fetchrow = AsyncMock(return_value=None)

    response = await async_client.get("/api/v1/portfolio/snapshot/2025-01-01")

    assert response.status_code == 404
    assert "2025-01-01" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Portfolio Equity Curve                                                      #
# --------------------------------------------------------------------------- #


async def test_equity_curve_returns_merged_points(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    row = {
        "strategy_id": "csm-set-01",
        "metadata": (
            '{"equity_curve": [{"date": "2026-05-14", "value": "100"},'
            ' {"date": "2026-05-15", "value": "105"}]}'
        ),
    }
    mock_pool._conn.fetch = AsyncMock(return_value=[row])

    response = await async_client.get("/api/v1/portfolio/equity-curve")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1


async def test_equity_curve_no_active_strategies_returns_empty(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    empty_registry = StrategyRegistry(strategies=[])
    monkeypatch.setattr("src.api.v1.portfolio.get_registry", lambda: empty_registry)

    response = await async_client.get("/api/v1/portfolio/equity-curve")

    assert response.status_code == 200
    assert response.json() == []


# --------------------------------------------------------------------------- #
# Error paths                                                                 #
# --------------------------------------------------------------------------- #


async def test_latest_snapshot_cache_set_fails_still_returns_200(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)
    mock_set = AsyncMock(side_effect=CacheError("redis down"))
    monkeypatch.setattr("src.api.v1.portfolio.set_cached", mock_set)

    row = _make_snapshot_row()
    mock_pool._conn.fetchrow = AsyncMock(return_value=row)

    response = await async_client.get("/api/v1/portfolio/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["active_strategies"] == 1
    mock_set.assert_awaited_once()


async def test_snapshot_by_date_cache_set_fails_still_returns_200(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)
    mock_set = AsyncMock(side_effect=CacheError("redis down"))
    monkeypatch.setattr("src.api.v1.portfolio.set_cached", mock_set)

    row = _make_snapshot_row()
    mock_pool._conn.fetchrow = AsyncMock(return_value=row)

    response = await async_client.get("/api/v1/portfolio/snapshot/2026-05-15")

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_date"] == "2026-05-15"


async def test_latest_snapshot_db_failure_returns_500(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    from src.services.errors import ServiceError

    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)

    mock_query = AsyncMock(side_effect=ServiceError("connection refused"))
    monkeypatch.setattr("src.api.v1.portfolio.query_latest_snapshot", mock_query)

    response = await async_client.get("/api/v1/portfolio/snapshot")

    assert response.status_code == 500
    assert "Failed to query portfolio snapshot" in response.json()["detail"]


async def test_equity_curve_db_failure_returns_500(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    from src.services.errors import ServiceError

    mock_compute = AsyncMock(side_effect=ServiceError("connection refused"))
    monkeypatch.setattr("src.api.v1.portfolio.compute_portfolio_equity_curve", mock_compute)

    response = await async_client.get("/api/v1/portfolio/equity-curve")

    assert response.status_code == 500
    assert "Failed to compute portfolio equity curve" in response.json()["detail"]
