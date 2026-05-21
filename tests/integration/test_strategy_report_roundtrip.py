"""End-to-end integration test for the strategy-report endpoints.

Uses the in-process ASGI client (no real network) and mocked Postgres /
Redis. Round-trips the full ingest → DB → cache → read flow for the
three new endpoints introduced in feature-strategies-report-metrics
Phase 3.

Auto-marked ``integration`` by the conftest.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from src.db import csm_set_postgres as csm_pg
from src.db import postgres as pg
from src.db import redis_client as rc
from src.schemas.strategy_report import StrategyReport

from tests.schemas.test_strategy import _report_dict


@pytest.fixture
def patch_lifespan_deps(
    monkeypatch: pytest.MonkeyPatch,
    mock_pool: MagicMock,
    mock_csm_set_pool: MagicMock,
) -> None:
    """Mock all DB/Redis deps so the lifespan starts cleanly."""

    async def _get_pool() -> Any:
        return mock_pool

    async def _get_csm_set_pool() -> Any:
        return mock_csm_set_pool

    async def _get_redis() -> AsyncMock:
        return AsyncMock()

    monkeypatch.setattr(pg, "get_pool", _get_pool)
    monkeypatch.setattr(csm_pg, "get_csm_set_pool", _get_csm_set_pool)
    monkeypatch.setattr("src.api.v1.ingest.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.strategy_report.get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.strategy_report.get_csm_set_pool", _get_csm_set_pool)
    monkeypatch.setattr("src.main.get_pool", _get_pool)
    monkeypatch.setattr("src.main.get_csm_set_pool", _get_csm_set_pool)
    monkeypatch.setattr(rc, "get_redis", _get_redis)
    monkeypatch.setattr("src.main.get_redis", _get_redis)


@pytest.mark.usefixtures("patch_lifespan_deps", "load_test_registry")
class TestStrategyReportRoundtrip:
    """Full ingest → report → trades → benchmark flow."""

    async def test_ingest_with_report_then_read_back(
        self,
        integration_client: AsyncClient,
        mock_pool: MagicMock,
        mock_csm_set_pool: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST a payload carrying ``extended_data.report``; then GET it back."""
        # 1. POST: ingestion writes daily_performance AND strategy_report_snapshot.
        report_dict = _report_dict()
        payload = {
            "strategy_metadata": {
                "id": "csm-set-01",
                "type": "equity-long",
                "last_updated": "2026-05-20T11:00:00+00:00",
            },
            "performance_metrics": {
                "daily_pnl": "1500.00",
                "equity_curve": [
                    {"date": "2026-05-19", "value": "100000.00"},
                    {"date": "2026-05-20", "value": "101500.00"},
                ],
                "max_drawdown": "-0.025",
                "sharpe_ratio": "1.6",
            },
            "current_exposure": {
                "total_value": "101500.00",
                "cash_balance": "50000.00",
                "positions_count": 5,
            },
            "extended_data": {"report": report_dict},
        }
        # Stub the bundle-invalidator so the ingest does not try real Redis.
        monkeypatch.setattr("src.api.v1.ingest.invalidate_strategy_report_bundle", AsyncMock())

        resp = await integration_client.post(
            "/api/v1/ingest/daily-report",
            json=payload,
            headers={"X-API-Key": "test-internal-api-key"},
        )
        assert resp.status_code == 201

        # 2. Both UPSERTs ran within one transaction.
        assert mock_pool._conn.execute.await_count == 2
        sqls = [c.args[0] for c in mock_pool._conn.execute.await_args_list]
        assert any("daily_performance" in s for s in sqls)
        assert any("strategy_report_snapshot" in s for s in sqls)
        mock_pool._conn.transaction.assert_called_once()

        # 3. GET /report returns the same report via the gateway pool.
        when = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
        stored_json = json.dumps(report_dict)
        mock_pool._conn.fetchrow.return_value = {
            "time": when,
            "report": stored_json,
            "computed_at": when,
        }
        # Force cache miss.
        monkeypatch.setattr("src.api.v1.strategy_report.get_cached", AsyncMock(return_value=None))
        monkeypatch.setattr("src.api.v1.strategy_report.set_cached", AsyncMock())

        resp = await integration_client.get("/api/v1/strategies/csm-set-01/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["strategy_id"] == "csm-set-01"
        # The shape matches the inbound report.
        expected = report_dict["headline"]
        assert isinstance(expected, dict)
        assert body["report"]["headline"]["total_trades"] == expected["total_trades"]
        # Verify the parsed StrategyReport round-trips faithfully.
        StrategyReport.model_validate(body["report"])

        # 4. GET /trades returns the paged log via the csm_set pool.
        mock_csm_set_pool._conn.fetchval.return_value = 1
        mock_csm_set_pool._conn.fetch.return_value = [
            {
                "time": when,
                "symbol": "PTT.BK",
                "side": "LONG",
                "quantity": Decimal("100"),
                "entry_price": Decimal("34.50"),
                "exit_price": Decimal("35.00"),
                "realized_pnl": Decimal("50.00"),
                "duration_bars": 3,
                "commission": Decimal("2.00"),
            }
        ]
        resp = await integration_client.get("/api/v1/strategies/csm-set-01/trades?limit=10")
        assert resp.status_code == 200
        page = resp.json()
        assert page["total"] == 1
        assert page["items"][0]["symbol"] == "PTT.BK"

        # 5. GET /benchmark-curve returns base-100 normalised values.
        mock_csm_set_pool._conn.fetch.return_value = [
            {"time": when, "equity": Decimal("200")},
            {"time": when, "equity": Decimal("220")},
        ]
        resp = await integration_client.get(
            "/api/v1/strategies/csm-set-01/benchmark-curve?normalize=true"
        )
        assert resp.status_code == 200
        curve = resp.json()
        assert len(curve) == 2
        assert float(curve[0]["value"]) == 100.0
        assert float(curve[1]["value"]) == 110.0
