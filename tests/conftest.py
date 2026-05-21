"""Shared pytest fixtures.

The fixtures here are:

* :func:`set_env` — autouse fixture that injects every required environment
  variable so :func:`src.config.get_settings` can be called from anywhere
  in the test suite without raising ``ValidationError``.
* :func:`async_client` — an :class:`httpx.AsyncClient` wired to the
  FastAPI app via :class:`httpx.ASGITransport`, so tests can hit endpoints
  in-process without binding a TCP port.
* :func:`load_test_registry` — populate ``src.services.strategy_registry``
  with the 3-strategy fixture so router tests have a known registry.
* :func:`mock_pool` — a fully-mocked ``asyncpg.Pool`` whose ``acquire`` /
  ``execute`` / ``fetch`` methods are :class:`AsyncMock`s. Tests use this
  to exercise SQL-bearing code without touching Postgres.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from src.config import get_settings
from src.main import app
from src.services import strategy_registry as registry_mod

_FIXTURE_REGISTRY_PATH = Path(__file__).parent / "strategies.fixture.json"

_TEST_ENV: dict[str, str] = {
    "POSTGRES_DSN": "postgresql://postgres:test@quant-postgres:5432/db_gateway",
    "MONGO_URI": "mongodb://quant-mongo:27017/",
    "REDIS_URL": "redis://quant-redis:6379/0",
    "CSM_SET_DSN": "postgresql://gateway_ro:test@quant-postgres:5432/db_csm_set",
    "CSM_SET_SERVICE_URL": "http://quant-csm-set:8001",
    "INTERNAL_API_KEY": "test-internal-api-key",
    "LOG_LEVEL": "INFO",
    "STRATEGY_REGISTRY_PATH": str(_FIXTURE_REGISTRY_PATH),
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


@pytest.fixture
def load_test_registry() -> Iterator[None]:
    """Load the 3-strategy test fixture into the registry module-global.

    Use this in router/api tests that need ``get_registry()`` to succeed.
    """
    reg = registry_mod.load_registry(_FIXTURE_REGISTRY_PATH)
    registry_mod.set_registry(reg)
    try:
        yield
    finally:
        registry_mod.clear_registry()


def _build_pool_mock(label: str) -> MagicMock:
    """Construct an asyncpg.Pool-shaped MagicMock.

    Factored out of :func:`mock_pool` so that each pool the test layer needs
    (e.g. ``db_gateway`` and ``db_csm_set``) is its own independent mock.
    Includes ``conn.transaction()`` as an async context manager so callers
    that wrap writes in ``async with conn.transaction(): ...`` work
    transparently.
    """
    conn = AsyncMock(name=f"{label}-connection")
    conn.execute = AsyncMock(name=f"{label}-conn-execute")
    conn.fetch = AsyncMock(name=f"{label}-conn-fetch", return_value=[])
    conn.fetchrow = AsyncMock(name=f"{label}-conn-fetchrow", return_value=None)
    conn.fetchval = AsyncMock(name=f"{label}-conn-fetchval", return_value=None)

    tx_ctx = AsyncMock(name=f"{label}-tx-ctx")
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx_ctx)

    acquire_ctx = AsyncMock(name=f"{label}-acquire-ctx")
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock(name=f"{label}-pool")
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool.execute = AsyncMock(name=f"{label}-pool-execute")
    pool.fetch = AsyncMock(name=f"{label}-pool-fetch", return_value=[])
    pool.fetchrow = AsyncMock(name=f"{label}-pool-fetchrow", return_value=None)
    pool._conn = conn
    return pool


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return an :class:`asyncpg.Pool`-shaped mock for ``db_gateway``.

    The returned object exposes:

    * ``pool.acquire()`` as an async context manager yielding a connection;
    * ``conn.execute`` / ``conn.fetch`` / ``conn.fetchrow`` / ``conn.fetchval``
      as :class:`AsyncMock`s (assertable);
    * ``conn.transaction()`` as an async context manager so callers that wrap
      writes in ``async with conn.transaction(): ...`` work transparently;
    * ``pool.execute`` / ``pool.fetch`` directly mocked too — both call styles
      are supported by ``asyncpg.Pool``.

    Tests can inspect call history via the standard ``mock.assert_*`` helpers.
    """
    return _build_pool_mock("asyncpg")


@pytest.fixture
def mock_csm_set_pool() -> MagicMock:
    """Return an :class:`asyncpg.Pool`-shaped mock for ``db_csm_set``.

    Mirrors :func:`mock_pool` but is an independent mock — tests can use
    both fixtures simultaneously to assert that gateway-side reads target
    the gateway pool and ``db_csm_set`` reads target this pool.
    """
    return _build_pool_mock("csm-set")


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


def _mock_pool_payload(pool: MagicMock) -> Any:
    """Return the underlying connection inside ``mock_pool`` for assertions."""
    return pool._conn
