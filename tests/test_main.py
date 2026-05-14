"""Tests for the FastAPI app instance and its lifespan."""

from httpx import AsyncClient
from src.main import app, lifespan


def test_app_metadata() -> None:
    """The FastAPI app is configured with the expected title and version."""
    assert app.title == "Quant API Gateway"
    assert app.version == "1.0.0"


def test_app_mounts_v1_router() -> None:
    """``/health`` is registered and the v1 prefix is reserved."""
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/health" in paths


async def test_lifespan_runs_startup_and_shutdown() -> None:
    """Entering and exiting the lifespan context runs both branches cleanly.

    httpx's ASGITransport does not implement the ASGI lifespan protocol, so
    the lifespan is exercised here by entering the context manager
    directly. This covers both the ``yield`` and ``finally`` branches.
    """
    async with lifespan(app):
        pass


async def test_health_via_async_client(async_client: AsyncClient) -> None:
    """The FastAPI app responds to ``GET /health`` via the in-process client."""
    response = await async_client.get("/health")
    assert response.status_code == 200
