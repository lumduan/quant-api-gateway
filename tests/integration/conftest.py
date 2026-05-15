"""Shared fixtures for integration tests.

All tests in this directory are automatically marked with
``@pytest.mark.integration`` so the default ``uv run pytest`` invocation
excludes them. Run integration tests explicitly with::

    uv run pytest -m integration -v

Fixtures that depend on external infrastructure (``DATABASE_URL`` /
``REDIS_URL`` env vars) skip when the required variable is not set.
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from src.main import app

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_REDIS_URL = os.environ.get("REDIS_URL", "")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test under ``tests/integration/`` with ``integration``."""
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest_asyncio.fixture
async def integration_client() -> AsyncIterator[AsyncClient]:
    """Yield an in-process :class:`httpx.AsyncClient` against the FastAPI app.

    Uses :class:`ASGITransport` so the full ASGI stack (middleware, lifespan,
    routers) is exercised without a separate TCP listener.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def require_postgres() -> None:
    """Skip the test when ``DATABASE_URL`` is not set."""
    if not _DATABASE_URL:
        pytest.skip("DATABASE_URL not set")


@pytest.fixture
def require_redis() -> None:
    """Skip the test when ``REDIS_URL`` is not set."""
    if not _REDIS_URL:
        pytest.skip("REDIS_URL not set")
