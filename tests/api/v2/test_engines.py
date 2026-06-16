"""Tests for the v2 engine-scoped API endpoints.

Covers parity between v1 and v2 endpoints, stub engine responses, and the
engine catalog endpoint. Uses existing conftest.py fixtures exclusively.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import AsyncClient
from src.api.v2.engines import market_data as md
from src.db import csm_set_postgres as csm_pg
from src.db import postgres as pg
from src.db import redis_client as rc
from src.schemas.gateway import MetricItem, PortfolioMetricsResponse
from src.schemas.registry import StrategyRegistry
from src.schemas.strategy_report import (
    StrategyReport,
    StrategyReportResponse,
    TradeLogPage,
)
from src.services.errors import CacheError
from src.utils.formatting import format_currency, format_delta_number, format_percentage

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
# Market-data proxy endpoints                                                 #
# --------------------------------------------------------------------------- #


class _FakeUpstream:
    """Stand-in for the shared httpx client used by the market-data proxy."""

    def __init__(
        self, *, response: httpx.Response | None = None, exc: Exception | None = None
    ) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def get(self, path: str, *, params: Any = None, headers: Any = None) -> httpx.Response:
        self.calls.append(
            {"path": path, "params": dict(params or {}), "headers": dict(headers or {})}
        )
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


@pytest.fixture(autouse=True)
def _reset_md_client() -> Any:
    """Ensure no real upstream client leaks across market-data proxy tests."""
    md._client = None
    yield
    md._client = None


def _patch_upstream(monkeypatch: pytest.MonkeyPatch, fake: _FakeUpstream) -> None:
    monkeypatch.setattr(md, "_get_client", lambda: fake)


async def test_market_data_health_proxied(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /health forwards the upstream engine's readiness payload."""
    fake = _FakeUpstream(
        response=httpx.Response(
            200, json={"status": "ok", "db": True, "redis": True, "cookie_present": False}
        )
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/market-data/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert fake.calls[0]["path"] == "/health"


async def test_market_data_ohlcv_forwards_params_and_api_key(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Query params and X-API-Key are forwarded to the engine."""
    fake = _FakeUpstream(
        response=httpx.Response(200, json={"symbol": "SET:PTT", "timeframe": "1d", "bars": []})
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/market-data/ohlcv",
        params={"symbol": "SET:PTT", "timeframe": "1d"},
        headers={"X-API-Key": "k123"},
    )
    assert response.status_code == 200
    call = fake.calls[0]
    assert call["path"] == "/ohlcv"
    assert call["params"] == {"symbol": "SET:PTT", "timeframe": "1d"}
    assert call["headers"].get("X-API-Key") == "k123"


async def test_market_data_settlements_proxied(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /settlements/{symbol} forwards the symbol path + X-API-Key to the engine."""
    fake = _FakeUpstream(
        response=httpx.Response(200, json={"symbol": "S50M26", "settlement_price": "1032.9"})
    )
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get(
        "/api/v2/engines/market-data/settlements/S50M26",
        headers={"X-API-Key": "k123"},
    )
    assert response.status_code == 200
    assert response.json()["settlement_price"] == "1032.9"
    call = fake.calls[0]
    assert call["path"] == "/settlements/S50M26"
    assert call["headers"].get("X-API-Key") == "k123"


async def test_market_data_adjusted_proxied(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, json={"adjusted": True, "bars": []}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/market-data/ohlcv/adjusted")
    assert response.status_code == 200 and response.json()["adjusted"] is True
    assert fake.calls[0]["path"] == "/ohlcv/adjusted"


async def test_market_data_4xx_passthrough(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream 4xx (e.g. auth) passes through with its status + body."""
    fake = _FakeUpstream(response=httpx.Response(401, json={"detail": "invalid API key"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/market-data/ohlcv")
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid API key"


async def test_market_data_upstream_5xx_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(500, json={"detail": "boom"}))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/market-data/health")
    assert response.status_code == 502


async def test_market_data_timeout_maps_to_504(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(exc=httpx.TimeoutException("slow"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/market-data/health")
    assert response.status_code == 504


async def test_market_data_connect_error_maps_to_503(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(exc=httpx.ConnectError("refused"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/market-data/universe")
    assert response.status_code == 503
    assert fake.calls[0]["path"] == "/universe"


async def test_market_data_invalid_json_maps_to_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeUpstream(response=httpx.Response(200, content=b"not json"))
    _patch_upstream(monkeypatch, fake)
    response = await async_client.get("/api/v2/engines/market-data/health")
    assert response.status_code == 502


async def test_market_data_close_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """close_market_data_client closes and clears the shared client."""
    real = md._get_client()
    assert md._client is real
    await md.close_market_data_client()
    assert md._client is None


async def test_market_data_providers_static(async_client: AsyncClient) -> None:
    """GET /providers stays a static informational endpoint (backward compat)."""
    response = await async_client.get("/api/v2/engines/market-data/providers")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert "settfex" in body["providers"] and "tvkit" in body["providers"]


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


async def test_catalog_returns_all_five_engines(async_client: AsyncClient) -> None:
    """GET /api/v2/engines/catalog returns all five engine slugs (static fallback)."""
    response = await async_client.get("/api/v2/engines/catalog")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 5

    slugs = {entry["slug"] for entry in body}
    assert slugs == {"market-data", "backtest", "portfolio", "signals", "execution"}

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


# --------------------------------------------------------------------------- #
# Formatting utility (src/utils/formatting.py)                                #
# --------------------------------------------------------------------------- #


def test_format_percentage_positive() -> None:
    assert format_percentage(Decimal("0.006262")) == "↑ +0.63%"


def test_format_percentage_negative() -> None:
    assert format_percentage(Decimal("-0.005")) == "↓ -0.50%"


def test_format_percentage_zero() -> None:
    assert format_percentage(Decimal("0")) == "→ 0.00%"


def test_format_percentage_rounding_half_up() -> None:
    """0.0199999 → 1.99999% → round-half-up → 2.00% → ``↑ +2.00%``."""
    assert format_percentage(Decimal("0.0199999")) == "↑ +2.00%"


def test_format_percentage_no_arrows_positive() -> None:
    """No-arrows mode matches OpenBB Metric widget value cell: no leading + for positives."""
    assert format_percentage(Decimal("0.005"), use_arrows=False) == "0.50%"


def test_format_percentage_no_arrows_negative() -> None:
    assert format_percentage(Decimal("-0.005"), use_arrows=False) == "-0.50%"


def test_format_percentage_no_arrows_zero() -> None:
    assert format_percentage(Decimal("0"), use_arrows=False) == "0.00%"


def test_format_percentage_custom_decimals() -> None:
    assert format_percentage(Decimal("0.123456"), decimals=4) == "↑ +12.3456%"


def test_format_currency_basic() -> None:
    assert format_currency(Decimal("998142.7124")) == "$998,142.71"


def test_format_currency_large_rounds_up() -> None:
    assert format_currency(Decimal("999999999.999")) == "$1,000,000,000.00"


def test_format_currency_negative_sign_before_dollar() -> None:
    assert format_currency(Decimal("-1234.5")) == "-$1,234.50"


def test_format_currency_zero() -> None:
    assert format_currency(Decimal("0")) == "$0.00"


def test_format_delta_number_positive() -> None:
    assert format_delta_number(Decimal("0.12")) == "0.12"


def test_format_delta_number_negative() -> None:
    assert format_delta_number(Decimal("-0.12")) == "-0.12"


def test_format_delta_number_zero() -> None:
    assert format_delta_number(Decimal("0")) == "0.00"


def test_format_delta_number_currency_amount_no_separator() -> None:
    """Delta cell has no thousands separator per OpenBB widget example."""
    assert format_delta_number(Decimal("6234.10")) == "6234.10"


def test_format_delta_number_rounding_half_up() -> None:
    assert format_delta_number(Decimal("0.125")) == "0.13"


# --------------------------------------------------------------------------- #
# Portfolio metrics endpoint (v2)                                             #
# --------------------------------------------------------------------------- #


def _make_metrics_snapshot_row(
    total_portfolio: float = 998142.7124,
    weighted_return: float = 0.006262,
    combined_drawdown: float | None = -0.0422,
    active_strategies: int = 1,
    time: datetime | None = None,
) -> dict[str, Any]:
    t = time or datetime(2026, 5, 22, 11, 0, 0, tzinfo=UTC)
    return {
        "time": t,
        "total_portfolio": total_portfolio,
        "weighted_return": weighted_return,
        "combined_drawdown": combined_drawdown,
        "active_strategies": active_strategies,
        "allocation": '{"csm-set-01": 1.0}',
    }


async def test_get_metrics_latest_with_previous_populates_delta(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """Latest metrics endpoint returns OpenBB-shape array with plain signed deltas."""
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())

    current = _make_metrics_snapshot_row(
        total_portfolio=998142.7124, weighted_return=0.006262, combined_drawdown=-0.0422
    )
    previous = _make_metrics_snapshot_row(
        total_portfolio=991908.6124,
        weighted_return=0.0075,
        combined_drawdown=-0.0410,
        time=datetime(2026, 5, 21, 11, 0, 0, tzinfo=UTC),
    )
    mock_pool._conn.fetchrow = AsyncMock(side_effect=[current, previous])

    response = await async_client.get("/api/v2/engines/portfolio/metrics")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 3

    daily, drawdown, total = body
    assert daily["label"] == "Daily Return"
    assert daily["value"] == "0.63%"
    assert daily["delta"] == "-0.12"

    assert drawdown["label"] == "Portfolio Drawdown"
    assert drawdown["value"] == "-4.22%"
    assert drawdown["delta"] == "-0.12"

    assert total["label"] == "Total Portfolio Value"
    assert total["value"] == "$998,142.71"
    assert total["delta"] == "6234.10"


async def test_get_metrics_query_param_snapshot_date_routes_to_by_date(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """``GET /metrics?snapshot_date=YYYY-MM-DD`` delegates to the by-date logic.

    Exists so OpenBB widget ``params`` (which always emit query strings) hit
    the same code path as the RESTful ``/metrics/{snapshot_date}`` route.
    """
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())

    current = _make_metrics_snapshot_row()
    mock_pool._conn.fetchrow = AsyncMock(side_effect=[current, None])

    response = await async_client.get("/api/v2/engines/portfolio/metrics?snapshot_date=2026-05-22")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert body[0]["label"] == "Daily Return"


async def test_get_metrics_by_date_no_previous_snapshot(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """When no previous snapshot exists, every delta is the empty string."""
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())

    current = _make_metrics_snapshot_row()
    mock_pool._conn.fetchrow = AsyncMock(side_effect=[current, None])

    response = await async_client.get("/api/v2/engines/portfolio/metrics/2026-05-22")

    assert response.status_code == 200
    body = response.json()
    assert all(item["delta"] == "" for item in body)
    assert body[0]["value"] == "0.63%"


async def test_get_metrics_null_drawdown_renders_na(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """A null ``combined_drawdown`` renders as ``N/A`` with an empty delta."""
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())

    current = _make_metrics_snapshot_row(combined_drawdown=None)
    previous = _make_metrics_snapshot_row(
        combined_drawdown=-0.0410, time=datetime(2026, 5, 21, 11, 0, 0, tzinfo=UTC)
    )
    mock_pool._conn.fetchrow = AsyncMock(side_effect=[current, previous])

    response = await async_client.get("/api/v2/engines/portfolio/metrics")

    assert response.status_code == 200
    drawdown = response.json()[1]
    assert drawdown["label"] == "Portfolio Drawdown"
    assert drawdown["value"] == "N/A"
    assert drawdown["delta"] == ""


async def test_get_metrics_drawdown_delta_skipped_when_previous_null(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """When the previous snapshot has null drawdown, drawdown delta is empty."""
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())

    current = _make_metrics_snapshot_row(combined_drawdown=-0.0422)
    previous = _make_metrics_snapshot_row(
        combined_drawdown=None, time=datetime(2026, 5, 21, 11, 0, 0, tzinfo=UTC)
    )
    mock_pool._conn.fetchrow = AsyncMock(side_effect=[current, previous])

    response = await async_client.get("/api/v2/engines/portfolio/metrics")

    assert response.status_code == 200
    drawdown = response.json()[1]
    assert drawdown["value"] == "-4.22%"
    assert drawdown["delta"] == ""


async def test_get_metrics_latest_returns_404_when_no_snapshots(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """Empty DB → 404 from /metrics."""
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())
    mock_pool._conn.fetchrow = AsyncMock(return_value=None)

    response = await async_client.get("/api/v2/engines/portfolio/metrics")

    assert response.status_code == 404


async def test_get_metrics_by_date_returns_404_when_missing(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """Missing snapshot for a specific date → 404."""
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())
    mock_pool._conn.fetchrow = AsyncMock(return_value=None)

    response = await async_client.get("/api/v2/engines/portfolio/metrics/2030-01-01")

    assert response.status_code == 404


async def test_get_metrics_cache_hit_skips_db(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """Cache hit returns the cached metrics array without touching Postgres."""
    cached = PortfolioMetricsResponse(
        snapshot_date=date(2026, 5, 22),
        metrics=[
            MetricItem(label="Daily Return", value="0.63%", delta=""),
            MetricItem(label="Portfolio Drawdown", value="-4.22%", delta=""),
            MetricItem(label="Total Portfolio Value", value="$998,142.71", delta=""),
        ],
        computed_at=datetime(2026, 5, 22, 11, 0, 0, tzinfo=UTC),
    )
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=cached))
    mock_pool._conn.fetchrow = AsyncMock(return_value=None)

    response = await async_client.get("/api/v2/engines/portfolio/metrics")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert body[0]["value"] == "0.63%"
    mock_pool._conn.fetchrow.assert_not_awaited()


async def test_get_metrics_negative_portfolio_delta(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """Portfolio value dropped vs previous → delta carries explicit minus."""
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())

    current = _make_metrics_snapshot_row(total_portfolio=991908.61)
    previous = _make_metrics_snapshot_row(
        total_portfolio=998142.71, time=datetime(2026, 5, 21, 11, 0, 0, tzinfo=UTC)
    )
    mock_pool._conn.fetchrow = AsyncMock(side_effect=[current, previous])

    response = await async_client.get("/api/v2/engines/portfolio/metrics")

    assert response.status_code == 200
    total = response.json()[2]
    assert total["delta"] == "-6234.10"


async def test_get_metrics_zero_portfolio_delta(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """Identical portfolio value vs previous → delta is ``"0.00"`` (no sign)."""
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v2.engines.portfolio.set_cached", AsyncMock())

    current = _make_metrics_snapshot_row(total_portfolio=998142.71, weighted_return=0.005)
    previous = _make_metrics_snapshot_row(
        total_portfolio=998142.71,
        weighted_return=0.005,
        time=datetime(2026, 5, 21, 11, 0, 0, tzinfo=UTC),
    )
    mock_pool._conn.fetchrow = AsyncMock(side_effect=[current, previous])

    response = await async_client.get("/api/v2/engines/portfolio/metrics")

    assert response.status_code == 200
    total = response.json()[2]
    assert total["delta"] == "0.00"


async def test_get_metrics_cache_set_failure_degrades_gracefully(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: Any,
) -> None:
    """A CacheError during set_cached still returns 200 with the fresh result."""
    monkeypatch.setattr("src.api.v2.engines.portfolio.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.api.v2.engines.portfolio.set_cached",
        AsyncMock(side_effect=CacheError("boom")),
    )

    current = _make_metrics_snapshot_row()
    mock_pool._conn.fetchrow = AsyncMock(side_effect=[current, None])

    response = await async_client.get("/api/v2/engines/portfolio/metrics")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 3
