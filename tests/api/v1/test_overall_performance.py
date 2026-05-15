"""Tests for ``GET /api/v1/overall-performance``."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from src.db import postgres as pg
from src.db import redis_client as rc
from src.schemas.gateway import OverallPerformanceResponse, StrategyPerformanceResponse
from src.schemas.registry import StrategyRegistry
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
    monkeypatch.setattr("src.api.v1.performance.get_pool", _get_pool)
    monkeypatch.setattr("src.main.get_pool", _get_pool)
    monkeypatch.setattr(rc, "get_redis", _get_redis)
    monkeypatch.setattr("src.main.get_redis", _get_redis)


def _make_strategy_perf_row(
    strategy_id: str = "csm-set-01",
    total_value: float = 100000.0,
    daily_return: float = 0.01,
    max_drawdown: float = -0.05,
    sharpe_ratio: float = 1.5,
    time: datetime | None = None,
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "total_value": total_value,
        "daily_return": daily_return,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "time": time or datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
        "metadata": (
            '{"daily_pnl": "1000", "equity_curve": [{"date": "2026-05-15", "value": "100000"}]}'
        ),
    }


def _make_cached_overall_response() -> OverallPerformanceResponse:
    return OverallPerformanceResponse(
        total_portfolio_value=Decimal("100000"),
        weighted_daily_return=Decimal("0.010000"),
        combined_max_drawdown=Decimal("-0.0500"),
        active_strategies=1,
        allocation={"csm-set-01": Decimal("1.0")},
        strategies=[
            StrategyPerformanceResponse(
                strategy_id="csm-set-01",
                daily_pnl=Decimal("1000"),
                total_value=Decimal("100000"),
                max_drawdown=Decimal("-0.0500"),
                sharpe_ratio=Decimal("1.5000"),
                last_updated=datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
            )
        ],
        computed_at=datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
    )


async def test_cache_hit_returns_cached_response(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    cached = _make_cached_overall_response()
    mock_get = AsyncMock(return_value=cached)
    monkeypatch.setattr("src.api.v1.performance.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.performance.set_cached", mock_set)

    response = await async_client.get("/api/v1/overall-performance")

    assert response.status_code == 200
    body = response.json()
    assert body["active_strategies"] == 1
    assert float(body["total_portfolio_value"]) == 100000.0
    mock_get.assert_awaited_once()
    mock_set.assert_not_awaited()
    # DB should not be queried on cache hit
    mock_pool._conn.fetch.assert_not_awaited()


async def test_cache_miss_queries_db_and_populates_cache(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.performance.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.performance.set_cached", mock_set)

    row = _make_strategy_perf_row()
    mock_pool._conn.fetch = AsyncMock(return_value=[row])

    response = await async_client.get("/api/v1/overall-performance")

    assert response.status_code == 200
    body = response.json()
    # Fixture registry has 2 active strategies
    assert body["active_strategies"] == 2
    mock_get.assert_awaited_once()
    mock_set.assert_awaited_once()


async def test_cache_miss_set_cached_fails_still_returns_200(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.performance.get_cached", mock_get)
    mock_set = AsyncMock(side_effect=CacheError("redis down"))
    monkeypatch.setattr("src.api.v1.performance.set_cached", mock_set)

    row = _make_strategy_perf_row()
    mock_pool._conn.fetch = AsyncMock(return_value=[row])

    response = await async_client.get("/api/v1/overall-performance")

    assert response.status_code == 200
    body = response.json()
    # Fixture registry has 2 active strategies
    assert body["active_strategies"] == 2
    mock_set.assert_awaited_once()


async def test_no_active_strategies_returns_zeroes(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.performance.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.performance.set_cached", mock_set)

    # Return a registry with no active strategies
    empty_registry = StrategyRegistry(strategies=[])
    monkeypatch.setattr("src.api.v1.performance.get_registry", lambda: empty_registry)

    response = await async_client.get("/api/v1/overall-performance")

    assert response.status_code == 200
    body = response.json()
    assert body["active_strategies"] == 0
    assert body["strategies"] == []


async def test_db_failure_returns_500(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    from src.services.errors import ServiceError

    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.performance.get_cached", mock_get)

    mock_compute = AsyncMock(side_effect=ServiceError("connection refused"))
    monkeypatch.setattr("src.api.v1.performance.compute_overall_performance", mock_compute)

    response = await async_client.get("/api/v1/overall-performance")

    assert response.status_code == 500
    assert "Failed to compute overall performance" in response.json()["detail"]
