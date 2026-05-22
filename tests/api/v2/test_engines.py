"""Tests for the v2 engine-scoped API endpoints.

Covers parity between v1 and v2 endpoints, stub engine responses, and the
engine catalog endpoint. Uses existing conftest.py fixtures exclusively.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from src.db import csm_set_postgres as csm_pg
from src.db import postgres as pg
from src.db import redis_client as rc
from src.schemas.registry import StrategyRegistry
from src.schemas.strategy_report import (
    StrategyReport,
    StrategyReportResponse,
    TradeLogPage,
)

from tests.schemas.test_strategy import _report_dict

# --------------------------------------------------------------------------- #
# Shared fixtures (reuse conftest.py patterns)                                #
# --------------------------------------------------------------------------- #


def _valid_report() -> StrategyReport:
    return StrategyReport.model_validate(_report_dict())


def _cached_report_response() -> StrategyReportResponse:
    when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    return StrategyReportResponse(
        strategy_id="csm-set-01",
        as_of=when,
        report=_valid_report(),
        computed_at=when,
    )


@pytest.fixture
def patch_lifespan_deps(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any, mock_csm_set_pool: Any
) -> None:
    """Wire up mock pool/redis so the v2 endpoints don't need a real DB."""

    async def _get_pool() -> Any:
        return mock_pool

    async def _get_csm_set_pool() -> Any:
        return mock_csm_set_pool

    async def _get_redis() -> AsyncMock:
        return AsyncMock()

    monkeypatch.setattr(pg, "get_pool", _get_pool)
    monkeypatch.setattr(csm_pg, "get_csm_set_pool", _get_csm_set_pool)
    # v1 paths
    monkeypatch.setattr("src.api.v1.portfolio.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.performance.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.strategies.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.strategy_report.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.strategy_report.get_csm_set_pool", _get_csm_set_pool)
    # v2 paths
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v2.engines.backtest.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v2.engines.backtest.get_csm_set_pool", _get_csm_set_pool)
    monkeypatch.setattr("src.api.v2.engines.catalog.get_pool", _get_pool)
    monkeypatch.setattr(rc, "get_redis", _get_redis)


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


def _normalize(data: Any) -> Any:
    """Strip volatile timestamp fields so v1/v2 parity checks are deterministic.

    ``computed_at`` and ``last_updated`` are set to ``datetime.now(UTC)`` at
    call time, so two sequential requests will differ at microsecond resolution.
    """
    if isinstance(data, dict):
        return {
            k: _normalize(v) for k, v in data.items() if k not in ("computed_at", "last_updated")
        }
    if isinstance(data, list):
        return [_normalize(item) for item in data]
    return data


# --------------------------------------------------------------------------- #
# Portfolio snapshot parity                                                   #
# --------------------------------------------------------------------------- #


async def test_latest_snapshot_v1_v2_parity(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """v1 and v2 latest snapshot return identical JSON."""
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.portfolio.set_cached", mock_set)
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", mock_set)

    row = _make_snapshot_row()
    mock_pool._conn.fetchrow = AsyncMock(return_value=row)

    v1_resp = await async_client.get("/api/v1/portfolio/snapshot")
    v2_resp = await async_client.get("/api/v2/engines/portfolio/snapshot")

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


async def test_snapshot_by_date_v1_v2_parity(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """v1 and v2 snapshot by date return identical JSON."""
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.portfolio.get_cached", mock_get)
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", mock_get)
    monkeypatch.setattr("src.api.v1.portfolio.set_cached", AsyncMock())
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())

    row = _make_snapshot_row()
    mock_pool._conn.fetchrow = AsyncMock(return_value=row)

    v1_resp = await async_client.get("/api/v1/portfolio/snapshot/2026-05-15")
    v2_resp = await async_client.get("/api/v2/engines/portfolio/snapshot/2026-05-15")

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


# --------------------------------------------------------------------------- #
# Portfolio equity curve parity                                               #
# --------------------------------------------------------------------------- #


async def test_equity_curve_v1_v2_parity(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    mock_pool: Any,
) -> None:
    """v1 and v2 equity curve return identical JSON."""
    row = {
        "strategy_id": "csm-set-01",
        "metadata": (
            '{"equity_curve": [{"date": "2026-05-14", "value": "100"},'
            ' {"date": "2026-05-15", "value": "105"}]}'
        ),
    }
    mock_pool._conn.fetch = AsyncMock(return_value=[row])

    v1_resp = await async_client.get("/api/v1/portfolio/equity-curve")
    v2_resp = await async_client.get("/api/v2/engines/portfolio/equity-curve")

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


# --------------------------------------------------------------------------- #
# Overall performance parity                                                  #
# --------------------------------------------------------------------------- #


async def test_overall_performance_v1_v2_parity(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """v1 and v2 overall-performance return identical JSON."""
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.performance.get_cached", mock_get)
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.performance.set_cached", mock_set)
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", mock_set)

    row = {
        "strategy_id": "csm-set-01",
        "total_value": 100000.0,
        "daily_return": 0.01,
        "max_drawdown": -0.05,
        "sharpe_ratio": 1.5,
        "time": datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
        "metadata": (
            '{"daily_pnl": "1000", '
            '"equity_curve": [{"date": "2026-05-14", "value": "100"},'
            ' {"date": "2026-05-15", "value": "105"}]}'
        ),
    }
    mock_pool._conn.fetch = AsyncMock(return_value=[row])

    v1_resp = await async_client.get("/api/v1/overall-performance")
    v2_resp = await async_client.get("/api/v2/engines/portfolio/overall-performance")

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


# --------------------------------------------------------------------------- #
# Strategy performance parity                                                 #
# --------------------------------------------------------------------------- #


async def test_strategy_performance_v1_v2_parity(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """v1 and v2 strategy performance return identical JSON."""
    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.performance.get_cached", mock_get)
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.performance.set_cached", mock_set)
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", mock_set)

    row = {
        "strategy_id": "csm-set-01",
        "total_value": 100000.0,
        "daily_return": 0.01,
        "max_drawdown": -0.05,
        "sharpe_ratio": 1.5,
        "time": datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
        "metadata": '{"daily_pnl": "1000"}',
    }
    mock_pool._conn.fetchrow = AsyncMock(return_value=row)

    v1_resp = await async_client.get("/api/v1/strategies/csm-set-01/performance")
    v2_resp = await async_client.get("/api/v2/engines/portfolio/strategies/csm-set-01/performance")

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


# --------------------------------------------------------------------------- #
# Strategy list parity                                                        #
# --------------------------------------------------------------------------- #


async def test_list_strategies_v1_v2_parity(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    """v1 and v2 strategy list return identical JSON."""
    v1_resp = await async_client.get("/api/v1/strategies")
    v2_resp = await async_client.get("/api/v2/engines/portfolio/strategies")

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


async def test_get_strategy_v1_v2_parity(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    """v1 and v2 single strategy return identical JSON."""
    v1_resp = await async_client.get("/api/v1/strategies/csm-set-01")
    v2_resp = await async_client.get("/api/v2/engines/portfolio/strategies/csm-set-01")

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


# --------------------------------------------------------------------------- #
# Strategy equity curve parity                                                #
# --------------------------------------------------------------------------- #


async def test_strategy_equity_curve_v1_v2_parity(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    mock_pool: Any,
) -> None:
    """v1 and v2 strategy equity curve return identical JSON."""
    row = {
        "strategy_id": "csm-set-01",
        "metadata": (
            '{"equity_curve": [{"date": "2026-05-14", "value": "100"},'
            ' {"date": "2026-05-15", "value": "105"}]}'
        ),
    }
    mock_pool._conn.fetchrow = AsyncMock(return_value=row)

    v1_resp = await async_client.get("/api/v1/strategies/csm-set-01/equity-curve")
    v2_resp = await async_client.get("/api/v2/engines/portfolio/strategies/csm-set-01/equity-curve")

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


# --------------------------------------------------------------------------- #
# Strategy report parity (backtest)                                           #
# --------------------------------------------------------------------------- #


async def test_strategy_report_v1_v2_parity(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1 and v2 strategy report return identical JSON — mock at service level."""
    cached = _cached_report_response()

    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", mock_get)
    monkeypatch.setattr("src.api.v2.engines.backtest.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.strategy_report.set_cached", mock_set)
    monkeypatch.setattr("src.api.v2.engines.backtest.set_cached", mock_set)

    mock_svc = AsyncMock(return_value=cached)
    monkeypatch.setattr("src.api.v1.strategy_report.get_latest_report", mock_svc)
    monkeypatch.setattr("src.api.v2.engines.backtest.get_latest_report", mock_svc)

    v1_resp = await async_client.get("/api/v1/strategies/csm-set-01/report")
    v2_resp = await async_client.get("/api/v2/engines/backtest/strategies/csm-set-01/report")

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


async def test_strategy_trades_v1_v2_parity(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1 and v2 trade log return identical JSON — mock at service level."""

    page = TradeLogPage(items=[], total=0, limit=10, offset=0)

    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", mock_get)
    monkeypatch.setattr("src.api.v2.engines.backtest.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.strategy_report.set_cached", mock_set)
    monkeypatch.setattr("src.api.v2.engines.backtest.set_cached", mock_set)

    mock_svc = AsyncMock(return_value=page)
    monkeypatch.setattr("src.api.v1.strategy_report.list_trades", mock_svc)
    monkeypatch.setattr("src.api.v2.engines.backtest.list_trades", mock_svc)

    v1_resp = await async_client.get("/api/v1/strategies/csm-set-01/trades?limit=10")
    v2_resp = await async_client.get(
        "/api/v2/engines/backtest/strategies/csm-set-01/trades?limit=10"
    )

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


async def test_strategy_benchmark_v1_v2_parity(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1 and v2 benchmark curve return identical JSON — mock at service level."""
    from src.schemas.strategy_report import BenchmarkPoint

    points: list[BenchmarkPoint] = []

    mock_get = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", mock_get)
    monkeypatch.setattr("src.api.v2.engines.backtest.get_cached", mock_get)
    mock_set = AsyncMock()
    monkeypatch.setattr("src.api.v1.strategy_report.set_cached", mock_set)
    monkeypatch.setattr("src.api.v2.engines.backtest.set_cached", mock_set)

    mock_svc = AsyncMock(return_value=points)
    monkeypatch.setattr("src.api.v1.strategy_report.get_benchmark_curve", mock_svc)
    monkeypatch.setattr("src.api.v2.engines.backtest.get_benchmark_curve", mock_svc)

    v1_resp = await async_client.get("/api/v1/strategies/csm-set-01/benchmark-curve")
    v2_resp = await async_client.get(
        "/api/v2/engines/backtest/strategies/csm-set-01/benchmark-curve"
    )

    assert v1_resp.status_code == 200
    assert v2_resp.status_code == 200
    assert _normalize(v1_resp.json()) == _normalize(v2_resp.json())


# --------------------------------------------------------------------------- #
# Market-data stub endpoints                                                  #
# --------------------------------------------------------------------------- #


async def test_market_data_health_returns_stub(async_client: AsyncClient) -> None:
    """GET /api/v2/engines/market-data/health returns stub status."""
    response = await async_client.get("/api/v2/engines/market-data/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stub"
    assert body["engine"] == "market-data"


async def test_market_data_providers_returns_stub(async_client: AsyncClient) -> None:
    """GET /api/v2/engines/market-data/providers returns stubbed providers."""
    response = await async_client.get("/api/v2/engines/market-data/providers")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert "settfex" in body["providers"]
    assert "tvkit" in body["providers"]


# --------------------------------------------------------------------------- #
# Signals stub endpoints                                                      #
# --------------------------------------------------------------------------- #


async def test_signals_health_returns_stub(async_client: AsyncClient) -> None:
    """GET /api/v2/engines/signals/health returns stub status."""
    response = await async_client.get("/api/v2/engines/signals/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stub"
    assert body["engine"] == "signals"


async def test_signals_status_returns_dormant(async_client: AsyncClient) -> None:
    """GET /api/v2/engines/signals/status returns dormant status."""
    response = await async_client.get("/api/v2/engines/signals/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dormant"
    assert "not yet active" in body["message"]


# --------------------------------------------------------------------------- #
# Catalog endpoint                                                            #
# --------------------------------------------------------------------------- #


async def test_catalog_returns_all_four_engines(async_client: AsyncClient) -> None:
    """GET /api/v2/engines/catalog returns all four engine slugs (static fallback)."""
    response = await async_client.get("/api/v2/engines/catalog")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 4

    slugs = {entry["slug"] for entry in body}
    assert slugs == {"market-data", "backtest", "portfolio", "signals"}

    for entry in body:
        assert "type" in entry
        assert "status" in entry
        assert "description" in entry


async def test_catalog_entries_have_expected_fields(async_client: AsyncClient) -> None:
    """Each catalog entry has slug, type, status, description fields."""
    response = await async_client.get("/api/v2/engines/catalog")

    assert response.status_code == 200
    for entry in response.json():
        assert isinstance(entry["slug"], str)
        assert entry["type"] in ("INTERNAL", "EXTERNAL")
        assert entry["status"] in ("active", "dormant")
        assert isinstance(entry["description"], str) and len(entry["description"]) > 0


# --------------------------------------------------------------------------- #
# v2 404 for missing strategies                                               #
# --------------------------------------------------------------------------- #


async def test_v2_portfolio_strategy_not_found_returns_404(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    """v2 strategy endpoint returns 404 for unknown strategy."""
    response = await async_client.get("/api/v2/engines/portfolio/strategies/nonexistent")
    assert response.status_code == 404


async def test_v2_backtest_strategy_not_found_returns_404(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    """v2 backtest endpoint returns 404 for unknown strategy."""
    response = await async_client.get("/api/v2/engines/backtest/strategies/nonexistent/report")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Empty registry edge cases                                                   #
# --------------------------------------------------------------------------- #


async def test_v2_equity_curve_empty_registry_returns_empty(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v2 equity curve returns empty list when no active strategies."""
    empty_registry = StrategyRegistry(strategies=[])
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_registry", lambda: empty_registry)

    response = await async_client.get("/api/v2/engines/portfolio/equity-curve")

    assert response.status_code == 200
    assert response.json() == []


async def test_v2_list_strategies_empty_registry(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v2 strategy list is empty when no active strategies."""
    empty_registry = StrategyRegistry(strategies=[])
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_registry", lambda: empty_registry)

    response = await async_client.get("/api/v2/engines/portfolio/strategies")

    assert response.status_code == 200
    assert response.json() == []
