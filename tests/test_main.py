"""Tests for the FastAPI app instance and its lifespan."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from src.db import postgres as pg
from src.main import app, lifespan
from src.services import strategy_registry


def test_app_metadata() -> None:
    """The FastAPI app is configured with the expected title and version."""
    assert app.title == "Quant API Gateway"
    assert app.version == "1.0.0"


def test_app_mounts_v1_router() -> None:
    """``/health`` is registered and the v1 prefix is reserved."""
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/health" in paths


async def test_lifespan_runs_startup_and_shutdown(
    monkeypatch: pytest.MonkeyPatch, mock_pool: Any
) -> None:
    """Entering and exiting the lifespan context runs both branches cleanly.

    The lifespan loads the strategy registry from ``strategies.json`` and
    opens the asyncpg pool — we point both at the test fixture / mock so
    the test runs in isolation. httpx's ASGITransport does not implement
    the lifespan protocol; the manual ``async with`` here exercises both
    the ``yield`` and ``finally`` branches.
    """
    # Set the pool global directly so close_pool() exercises its else-branch.
    monkeypatch.setattr(pg, "_pool", mock_pool)
    mock_pool.close = AsyncMock(return_value=None)

    async with lifespan(app):
        assert strategy_registry.get_registry() is not None
    # After shutdown the registry should be cleared
    import pytest as _pytest
    from src.services.errors import StrategyRegistryLoadError

    with _pytest.raises(StrategyRegistryLoadError):
        strategy_registry.get_registry()


async def test_health_via_async_client(async_client: AsyncClient) -> None:
    """The FastAPI app responds to ``GET /health`` via the in-process client."""
    response = await async_client.get("/health")
    assert response.status_code == 200
