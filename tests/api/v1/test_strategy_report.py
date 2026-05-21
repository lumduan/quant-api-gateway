"""Tests for ``/api/v1/strategies/{id}/{report,trades,benchmark-curve}``."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from src.db import csm_set_postgres as csm_pg
from src.db import postgres as pg
from src.db import redis_client as rc
from src.schemas.strategy_report import (
    BenchmarkPoint,
    StrategyReport,
    StrategyReportResponse,
    TradeLogEntry,
    TradeLogPage,
)
from src.services.errors import CacheError, ServiceError, StrategyReportNotFoundError

from tests.schemas.test_strategy import _report_dict


@pytest.fixture
def patch_lifespan_deps(
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: MagicMock,
    mock_csm_set_pool: MagicMock,
) -> None:
    """Patch every ``get_pool`` / ``get_csm_set_pool`` / ``get_redis`` import path."""

    async def _get_pool() -> Any:
        return mock_pool

    async def _get_csm_set_pool() -> Any:
        return mock_csm_set_pool

    async def _get_redis() -> AsyncMock:
        return AsyncMock()

    monkeypatch.setattr(pg, "get_pool", _get_pool)
    monkeypatch.setattr(csm_pg, "get_csm_set_pool", _get_csm_set_pool)
    monkeypatch.setattr("src.api.v1.strategy_report.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.strategy_report.get_csm_set_pool", _get_csm_set_pool)
    monkeypatch.setattr("src.main.get_pool", _get_pool)
    monkeypatch.setattr("src.main.get_csm_set_pool", _get_csm_set_pool)
    monkeypatch.setattr(rc, "get_redis", _get_redis)
    monkeypatch.setattr("src.main.get_redis", _get_redis)


def _valid_report() -> StrategyReport:
    return StrategyReport.model_validate(_report_dict())


def _cached_response() -> StrategyReportResponse:
    when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    return StrategyReportResponse(
        strategy_id="csm-set-01",
        as_of=when,
        report=_valid_report(),
        computed_at=when,
    )


# -----------------------------------------------------------------------------
# /report
# -----------------------------------------------------------------------------


async def test_report_returns_cached_when_present(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache hit short-circuits the DB read."""
    cached = _cached_response()
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=cached))
    set_mock = AsyncMock()
    monkeypatch.setattr("src.api.v1.strategy_report.set_cached", set_mock)
    svc_mock = AsyncMock()
    monkeypatch.setattr("src.api.v1.strategy_report.get_latest_report", svc_mock)

    resp = await async_client.get("/api/v1/strategies/csm-set-01/report")

    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_id"] == "csm-set-01"
    assert body["report"]["headline"]["total_trades"] == 5
    svc_mock.assert_not_awaited()
    set_mock.assert_not_awaited()


async def test_report_cache_miss_calls_service_and_sets_cache(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache miss path: service is called and the cache is set."""
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr("src.api.v1.strategy_report.set_cached", set_mock)
    svc_mock = AsyncMock(return_value=_cached_response())
    monkeypatch.setattr("src.api.v1.strategy_report.get_latest_report", svc_mock)

    resp = await async_client.get("/api/v1/strategies/csm-set-01/report")

    assert resp.status_code == 200
    svc_mock.assert_awaited_once()
    set_mock.assert_awaited_once()


async def test_report_with_date_calls_date_service(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``?date=YYYY-MM-DD`` is provided, the date-keyed service is called."""
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v1.strategy_report.set_cached", AsyncMock())
    svc_mock = AsyncMock(return_value=_cached_response())
    monkeypatch.setattr("src.api.v1.strategy_report.get_report_for_date", svc_mock)
    latest_mock = AsyncMock()
    monkeypatch.setattr("src.api.v1.strategy_report.get_latest_report", latest_mock)

    resp = await async_client.get("/api/v1/strategies/csm-set-01/report?date=2026-05-20")

    assert resp.status_code == 200
    svc_mock.assert_awaited_once()
    latest_mock.assert_not_awaited()


async def test_report_404_when_snapshot_missing(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``StrategyReportNotFoundError`` from the service maps to ``404``."""
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.api.v1.strategy_report.get_latest_report",
        AsyncMock(side_effect=StrategyReportNotFoundError("csm-set-01")),
    )

    resp = await async_client.get("/api/v1/strategies/csm-set-01/report")
    assert resp.status_code == 404
    assert "csm-set-01" in resp.json()["detail"]


async def test_report_404_for_unknown_strategy(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
) -> None:
    """Unknown strategy id returns ``404`` before any DB / cache call."""
    resp = await async_client.get("/api/v1/strategies/ghost/report")
    assert resp.status_code == 404


async def test_report_500_on_db_failure(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB failure during read surfaces as ``500``."""
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.api.v1.strategy_report.get_latest_report",
        AsyncMock(side_effect=ServiceError("boom")),
    )

    resp = await async_client.get("/api/v1/strategies/csm-set-01/report")
    assert resp.status_code == 500


async def test_report_cache_set_failure_degrades_gracefully(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If cache write fails, the response is still returned."""
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.api.v1.strategy_report.get_latest_report",
        AsyncMock(return_value=_cached_response()),
    )
    monkeypatch.setattr(
        "src.api.v1.strategy_report.set_cached",
        AsyncMock(side_effect=CacheError("redis down")),
    )

    resp = await async_client.get("/api/v1/strategies/csm-set-01/report")
    assert resp.status_code == 200


# -----------------------------------------------------------------------------
# /trades
# -----------------------------------------------------------------------------


def _trade_log_page(total: int = 2) -> TradeLogPage:
    when = datetime(2026, 5, 19, 9, 30, tzinfo=UTC)
    items = [
        TradeLogEntry(
            entry_time=when,
            exit_time=when,
            symbol="PTT.BK",
            side="LONG",
            qty=Decimal("100"),
            entry_price=Decimal("34.5"),
            exit_price=Decimal("35.0"),
            realized_pnl=Decimal("50"),
            duration_bars=3,
            commission=Decimal("2"),
        )
    ] * total
    return TradeLogPage(items=items, total=total, limit=10, offset=0)


async def test_trades_cache_hit(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.api.v1.strategy_report.get_cached",
        AsyncMock(return_value=_trade_log_page()),
    )
    svc_mock = AsyncMock()
    monkeypatch.setattr("src.api.v1.strategy_report.list_trades", svc_mock)

    resp = await async_client.get("/api/v1/strategies/csm-set-01/trades")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    svc_mock.assert_not_awaited()


async def test_trades_cache_miss_computes(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v1.strategy_report.set_cached", AsyncMock())
    svc_mock = AsyncMock(return_value=_trade_log_page())
    monkeypatch.setattr("src.api.v1.strategy_report.list_trades", svc_mock)

    resp = await async_client.get("/api/v1/strategies/csm-set-01/trades?limit=10&offset=0")
    assert resp.status_code == 200
    svc_mock.assert_awaited_once()


async def test_trades_404_for_unknown_strategy(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
) -> None:
    resp = await async_client.get("/api/v1/strategies/ghost/trades")
    assert resp.status_code == 404


async def test_trades_500_on_db_failure(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.api.v1.strategy_report.list_trades",
        AsyncMock(side_effect=ServiceError("boom")),
    )

    resp = await async_client.get("/api/v1/strategies/csm-set-01/trades")
    assert resp.status_code == 500


async def test_trades_rejects_invalid_limit(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
) -> None:
    """FastAPI rejects ``limit`` outside [1, 1000] with ``422``."""
    resp = await async_client.get("/api/v1/strategies/csm-set-01/trades?limit=0")
    assert resp.status_code == 422
    resp = await async_client.get("/api/v1/strategies/csm-set-01/trades?limit=1001")
    assert resp.status_code == 422


async def test_trades_cache_set_failure_degrades_gracefully(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.api.v1.strategy_report.list_trades",
        AsyncMock(return_value=_trade_log_page()),
    )
    monkeypatch.setattr(
        "src.api.v1.strategy_report.set_cached",
        AsyncMock(side_effect=CacheError("down")),
    )

    resp = await async_client.get("/api/v1/strategies/csm-set-01/trades")
    assert resp.status_code == 200


# -----------------------------------------------------------------------------
# /benchmark-curve
# -----------------------------------------------------------------------------


def _curve() -> list[BenchmarkPoint]:
    t = datetime(2026, 5, 1, tzinfo=UTC)
    return [BenchmarkPoint(date=t, value=Decimal("100"))]


async def test_benchmark_cache_hit(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.schemas.strategy_report import BenchmarkCurveResponse

    monkeypatch.setattr(
        "src.api.v1.strategy_report.get_cached",
        AsyncMock(return_value=BenchmarkCurveResponse(items=_curve())),
    )
    svc_mock = AsyncMock()
    monkeypatch.setattr("src.api.v1.strategy_report.get_benchmark_curve", svc_mock)

    resp = await async_client.get("/api/v1/strategies/csm-set-01/benchmark-curve")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert float(body[0]["value"]) == 100.0
    svc_mock.assert_not_awaited()


async def test_benchmark_cache_miss_computes(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("src.api.v1.strategy_report.set_cached", AsyncMock())
    svc_mock = AsyncMock(return_value=_curve())
    monkeypatch.setattr("src.api.v1.strategy_report.get_benchmark_curve", svc_mock)

    resp = await async_client.get("/api/v1/strategies/csm-set-01/benchmark-curve?normalize=true")
    assert resp.status_code == 200
    svc_mock.assert_awaited_once()
    # normalize flag was passed through.
    call = svc_mock.await_args
    assert call is not None
    assert call.kwargs["normalize"] is True


async def test_benchmark_404_for_unknown_strategy(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
) -> None:
    resp = await async_client.get("/api/v1/strategies/ghost/benchmark-curve")
    assert resp.status_code == 404


async def test_benchmark_500_on_db_failure(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.api.v1.strategy_report.get_benchmark_curve",
        AsyncMock(side_effect=ServiceError("boom")),
    )

    resp = await async_client.get("/api/v1/strategies/csm-set-01/benchmark-curve")
    assert resp.status_code == 500


async def test_benchmark_cache_set_failure_degrades_gracefully(
    async_client: AsyncClient,
    patch_lifespan_deps: None,
    load_test_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.api.v1.strategy_report.get_benchmark_curve",
        AsyncMock(return_value=_curve()),
    )
    monkeypatch.setattr(
        "src.api.v1.strategy_report.set_cached",
        AsyncMock(side_effect=CacheError("down")),
    )

    resp = await async_client.get("/api/v1/strategies/csm-set-01/benchmark-curve")
    assert resp.status_code == 200


# -----------------------------------------------------------------------------
# helper coverage
# -----------------------------------------------------------------------------


def test_params_hash_is_stable() -> None:
    """The hash helper produces a stable 16-char string for identical inputs."""
    from src.api.v1.strategy_report import _params_hash

    h1 = _params_hash(a=1, b="x", c=None)
    h2 = _params_hash(c=None, b="x", a=1)
    assert h1 == h2
    assert len(h1) == 16
