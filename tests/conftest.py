"""Shared pytest fixtures.

Two fixtures live here:

* :func:`set_env` — autouse fixture that injects every required environment
  variable so :func:`src.config.get_settings` can be called from anywhere
  in the test suite without raising ``ValidationError``.
* :func:`async_client` — an :class:`httpx.AsyncClient` wired to the
  FastAPI app via :class:`httpx.ASGITransport`, so tests can hit endpoints
  in-process without binding a TCP port.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from src.config import get_settings
from src.main import app

_TEST_ENV: dict[str, str] = {
    "POSTGRES_DSN": "postgresql://postgres:test@quant-postgres:5432/db_gateway",
    "MONGO_URI": "mongodb://quant-mongo:27017/",
    "REDIS_URL": "redis://quant-redis:6379/0",
    "CSM_SET_SERVICE_URL": "http://quant-csm-set:8001",
    "INTERNAL_API_KEY": "test-internal-api-key",
    "LOG_LEVEL": "INFO",
}


@pytest.fixture(autouse=True)
def set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a complete set of required env vars and reset the Settings cache.

    The fixture is ``autouse`` so every test sees a deterministic
    environment. Individual tests can still call ``monkeypatch.delenv`` or
    ``monkeypatch.setenv`` to override.
    """
    for key, value in _TEST_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[AsyncClient]:
    """Yield an :class:`httpx.AsyncClient` bound to the FastAPI app.

    Uses :class:`httpx.ASGITransport` so requests go directly through the
    ASGI stack — no real network socket, no separate uvicorn process. The
    FastAPI lifespan is exercised by entering the ``AsyncClient`` context
    via ``async with``.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
