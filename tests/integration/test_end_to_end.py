"""Integration tests for the quant-api-gateway.

Tests exercise the full ASGI stack (middleware, lifespan, routers, services)
with mocked infrastructure, validating multi-request flows and end-to-end
behaviours that unit tests cannot easily verify.

Run with::

    uv run pytest -m integration -v
"""

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from src.db import postgres as pg
from src.db import redis_client as rc
from src.schemas.gateway import OverallPerformanceResponse, StrategyPerformanceResponse

_METADATA_TEMPLATE = (
    '{"daily_pnl": "1000", "equity_curve": [{"date": "2026-05-15", "value": "100000"}]}'
)

_EQ_METADATA_FIXTURE = (
    '{"equity_curve": ['
    '{"date": "2026-05-01", "value": "1000"}, '
    '{"date": "2026-05-02", "value": "1100"}'
    "]}"
)


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
    monkeypatch.setattr("src.api.v1.portfolio.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.strategies.get_pool", _get_pool)
    monkeypatch.setattr("src.main.get_pool", _get_pool)
    monkeypatch.setattr(rc, "get_redis", _get_redis)
    monkeypatch.setattr("src.main.get_redis", _get_redis)


def _strategy_perf_row(
    strategy_id: str = "csm-set-01",
    total_value: float = 100000.0,
    daily_return: float = 0.01,
    max_drawdown: float = -0.05,
    sharpe_ratio: float = 1.5,
    t: datetime | None = None,
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "total_value": total_value,
        "daily_return": daily_return,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "time": t or datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
        "metadata": _METADATA_TEMPLATE,
    }


@pytest.mark.usefixtures("patch_lifespan_deps", "load_test_registry")
class TestHealthEndpoint:
    async def test_health_returns_ok(self, integration_client: AsyncClient) -> None:
        """``GET /health`` returns 200 with ``{"status": "ok"}``."""
        response = await integration_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body == {"status": "ok"}


@pytest.mark.usefixtures("patch_lifespan_deps", "load_test_registry")
class TestOverallPerformanceCacheHit:
    async def test_cache_hit_under_200ms(
        self,
        integration_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Second request to /overall-performance is a cache hit, < 200 ms."""
        cached = OverallPerformanceResponse(
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
                    sharpe_ratio=Decimal("1.5"),
                    last_updated=datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
                )
            ],
            computed_at=datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
        )
        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=cached.model_dump_json())
        monkeypatch.setattr("src.services.cache.get_redis", AsyncMock(return_value=redis_mock))

        start = time.perf_counter()
        response = await integration_client.get("/api/v1/overall-performance")
        elapsed = (time.perf_counter() - start) * 1000

        assert response.status_code == 200
        assert elapsed < 200


@pytest.mark.usefixtures("patch_lifespan_deps", "load_test_registry")
class TestStrategyPerformanceRange:
    async def test_range_query_returns_list(
        self, integration_client: AsyncClient, mock_pool: Any
    ) -> None:
        """``?from_date=&to_date=`` returns a list of snapshots."""
        conn = mock_pool._conn
        conn.fetch = AsyncMock(return_value=[_strategy_perf_row()])

        response = await integration_client.get(
            "/api/v1/strategies/csm-set-01/performance",
            params={"from_date": "2026-05-01", "to_date": "2026-05-15"},
        )

        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["strategy_id"] == "csm-set-01"

    async def test_range_query_partial_params_returns_422(
        self, integration_client: AsyncClient
    ) -> None:
        """Only one of from_date/to_date → 422."""
        response = await integration_client.get(
            "/api/v1/strategies/csm-set-01/performance",
            params={"from_date": "2026-05-01"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "Both from_date and to_date are required" in detail

    async def test_range_query_empty_returns_empty_list(
        self, integration_client: AsyncClient, mock_pool: Any
    ) -> None:
        """No rows in range → empty list, not 404."""
        conn = mock_pool._conn
        conn.fetch = AsyncMock(return_value=[])

        response = await integration_client.get(
            "/api/v1/strategies/csm-set-01/performance",
            params={"from_date": "2020-01-01", "to_date": "2020-12-31"},
        )
        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.usefixtures("patch_lifespan_deps", "load_test_registry")
class TestPortfolioEquityCurveNoNormalize:
    async def test_normalize_false_returns_raw_values(
        self, integration_client: AsyncClient, mock_pool: Any
    ) -> None:
        """``?normalize=false`` returns raw cumulative values, not base-100."""
        conn = mock_pool._conn
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "strategy_id": "csm-set-01",
                    "metadata": _EQ_METADATA_FIXTURE,
                }
            ]
        )

        # With normalize=true (default), values are base-100 normalised
        resp_norm = await integration_client.get(
            "/api/v1/portfolio/equity-curve", params={"normalize": "true"}
        )
        assert resp_norm.status_code == 200
        norm_values = [p["value"] for p in resp_norm.json()]
        assert float(norm_values[0]) == 100.0

        # With normalize=false, raw cumulative values are preserved
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "strategy_id": "csm-set-01",
                    "metadata": _EQ_METADATA_FIXTURE,
                }
            ]
        )
        resp_raw = await integration_client.get(
            "/api/v1/portfolio/equity-curve", params={"normalize": "false"}
        )
        assert resp_raw.status_code == 200
        raw_values = [p["value"] for p in resp_raw.json()]
        assert float(raw_values[0]) == 1000.0


@pytest.mark.usefixtures("patch_lifespan_deps", "load_test_registry")
class TestCacheInvalidationOnIngest:
    async def test_ingest_flushes_cache(
        self,
        integration_client: AsyncClient,
        mock_pool: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A new ingestion clears ``overall_performance`` from Redis."""
        conn = mock_pool._conn
        conn.fetchrow = AsyncMock(return_value=_strategy_perf_row())
        conn.execute = AsyncMock(return_value="INSERT 1")
        conn.fetch = AsyncMock(return_value=[_strategy_perf_row()])

        # Pre-populate cache with a known response
        cached = OverallPerformanceResponse(
            total_portfolio_value=Decimal("999999"),
            weighted_daily_return=Decimal("0"),
            combined_max_drawdown=Decimal("0"),
            active_strategies=1,
            allocation={"csm-set-01": Decimal("1.0")},
            strategies=[],
            computed_at=datetime(2026, 5, 15, tzinfo=UTC),
        )

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=cached.model_dump_json())
        redis_mock.setex = AsyncMock()
        redis_mock.delete = AsyncMock()

        async def _mock_redis() -> AsyncMock:
            return redis_mock

        monkeypatch.setattr(rc, "get_redis", _mock_redis)
        monkeypatch.setattr("src.main.get_redis", _mock_redis)
        monkeypatch.setattr("src.services.cache.get_redis", _mock_redis)

        # Verify cache is populated
        resp1 = await integration_client.get("/api/v1/overall-performance")
        assert resp1.status_code == 200
        assert float(resp1.json()["total_portfolio_value"]) == 999999.0

        # Ingest changes — cache should be invalidated
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.setex = AsyncMock()

        ingest_payload = {
            "strategy_metadata": {
                "id": "csm-set-01",
                "type": "equity-long",
                "last_updated": "2026-05-15T11:00:00Z",
            },
            "performance_metrics": {
                "daily_pnl": "2000",
                "equity_curve": [{"date": "2026-05-15", "value": "102000"}],
                "max_drawdown": "-0.03",
                "sharpe_ratio": "1.8",
            },
            "current_exposure": {
                "total_value": "102000",
                "cash_balance": "20000",
                "positions_count": 5,
            },
        }
        resp_ingest = await integration_client.post(
            "/api/v1/ingest/daily-report",
            json=ingest_payload,
            headers={"X-API-Key": "test-internal-api-key"},
        )
        assert resp_ingest.status_code == 201

        # Next GET returns fresh data (old cached value gone)
        resp2 = await integration_client.get("/api/v1/overall-performance")
        assert resp2.status_code == 200
        assert float(resp2.json()["total_portfolio_value"]) != 999999.0
