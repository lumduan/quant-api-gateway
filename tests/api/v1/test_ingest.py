"""Integration-ish tests for ``POST /api/v1/ingest/daily-report``.

The asyncpg pool is mocked, but every other layer (router, dependency,
Pydantic validation, registry lookup) runs for real.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from src.db import postgres as pg
from src.services import snapshot_writer
from src.services.errors import ServiceError

_VALID_BODY: dict[str, Any] = {
    "strategy_metadata": {
        "id": "csm-set-01",
        "type": "equity-long",
        "last_updated": "2026-05-14T11:00:00Z",
    },
    "performance_metrics": {
        "daily_pnl": "15000.50",
        "equity_curve": [
            {"date": "2026-05-13", "value": "1035000.00"},
            {"date": "2026-05-14", "value": "1050000.00"},
        ],
        "max_drawdown": "-0.063",
        "sharpe_ratio": "1.85",
    },
    "current_exposure": {
        "total_value": "1050000.00",
        "cash_balance": "50000.00",
        "positions_count": 5,
    },
}


@pytest.fixture
def patch_pool(monkeypatch: pytest.MonkeyPatch, mock_pool: Any) -> Any:
    """Make ``get_pool()`` return the test ``mock_pool``."""

    async def _get_pool() -> Any:
        return mock_pool

    monkeypatch.setattr(pg, "get_pool", _get_pool)
    monkeypatch.setattr("src.api.v1.ingest.get_pool", _get_pool)
    return mock_pool


async def test_ingest_happy_path(
    async_client: AsyncClient,
    load_test_registry: None,
    patch_pool: Any,
) -> None:
    response = await async_client.post(
        "/api/v1/ingest/daily-report",
        headers={"X-API-Key": "test-internal-api-key"},
        json=_VALID_BODY,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {
        "status": "accepted",
        "strategy_id": "csm-set-01",
        "time": datetime(2026, 5, 14, 11, 0, tzinfo=UTC).isoformat(),
    }
    patch_pool._conn.execute.assert_awaited()  # at least the daily_performance upsert


async def test_ingest_missing_api_key(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    response = await async_client.post("/api/v1/ingest/daily-report", json=_VALID_BODY)
    assert response.status_code == 403


async def test_ingest_wrong_api_key(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    response = await async_client.post(
        "/api/v1/ingest/daily-report",
        headers={"X-API-Key": "wrong-key"},
        json=_VALID_BODY,
    )
    assert response.status_code == 403


async def test_ingest_invalid_body(
    async_client: AsyncClient,
    load_test_registry: None,
) -> None:
    response = await async_client.post(
        "/api/v1/ingest/daily-report",
        headers={"X-API-Key": "test-internal-api-key"},
        json={"strategy_metadata": {}},
    )
    assert response.status_code == 422


async def test_ingest_unknown_strategy(
    async_client: AsyncClient,
    load_test_registry: None,
    patch_pool: Any,
) -> None:
    body = {**_VALID_BODY, "strategy_metadata": {**_VALID_BODY["strategy_metadata"], "id": "nope"}}
    response = await async_client.post(
        "/api/v1/ingest/daily-report",
        headers={"X-API-Key": "test-internal-api-key"},
        json=body,
    )
    assert response.status_code == 404
    assert "Unknown strategy_id" in response.json()["detail"]
    patch_pool._conn.execute.assert_not_awaited()


async def test_ingest_snapshot_writer_failure_does_not_break_ingest(
    async_client: AsyncClient,
    load_test_registry: None,
    patch_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**kwargs: Any) -> bool:
        raise ServiceError("kaboom")

    monkeypatch.setattr(snapshot_writer, "maybe_write_snapshot", _boom)

    response = await async_client.post(
        "/api/v1/ingest/daily-report",
        headers={"X-API-Key": "test-internal-api-key"},
        json=_VALID_BODY,
    )
    assert response.status_code == 201


async def test_ingest_persist_error_returns_500(
    async_client: AsyncClient,
    load_test_registry: None,
    patch_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the daily_performance upsert raises ``IngestionPersistError`` we 500."""
    from src.services import ingestion
    from src.services.errors import IngestionPersistError

    async def _persist_fail(payload: Any, *, pool: Any) -> None:
        raise IngestionPersistError("denied")

    monkeypatch.setattr(ingestion, "persist_daily_report", _persist_fail)
    monkeypatch.setattr("src.api.v1.ingest.ingestion.persist_daily_report", _persist_fail)

    response = await async_client.post(
        "/api/v1/ingest/daily-report",
        headers={"X-API-Key": "test-internal-api-key"},
        json=_VALID_BODY,
    )
    assert response.status_code == 500
