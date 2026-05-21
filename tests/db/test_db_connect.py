import pytest


@pytest.fixture(autouse=True)
def _clear_db_state() -> None:
    """Reset each module's global state before every test so they don't leak."""
    import src.db.csm_set_postgres as csm_pg
    import src.db.mongo as mongo
    import src.db.postgres as pg
    import src.db.redis_client as redis_mod

    pg._pool = None
    csm_pg._pool = None
    mongo._client = None
    redis_mod._redis = None


# --- Postgres ---


@pytest.mark.anyio
async def test_postgres_pool_singleton() -> None:
    from unittest.mock import AsyncMock, patch

    with patch("src.db.postgres.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = AsyncMock(name="mock_pool")

        from src.db.postgres import get_pool

        pool1 = await get_pool()
        pool2 = await get_pool()
        assert pool1 is pool2
        mock_create.assert_awaited_once()


def test_postgres_close_pool() -> None:
    from unittest.mock import AsyncMock

    import src.db.postgres as pg

    pg._pool = AsyncMock(name="mock_pool")
    import asyncio

    asyncio.run(pg.close_pool())
    assert pg._pool is None


# --- CSM-set read-only pool ---


@pytest.mark.anyio
async def test_csm_set_postgres_pool_singleton() -> None:
    from unittest.mock import AsyncMock, patch

    with patch(
        "src.db.csm_set_postgres.asyncpg.create_pool", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = AsyncMock(name="mock_csm_set_pool")

        from src.db.csm_set_postgres import get_csm_set_pool

        pool1 = await get_csm_set_pool()
        pool2 = await get_csm_set_pool()
        assert pool1 is pool2
        mock_create.assert_awaited_once()


def test_csm_set_postgres_close_pool() -> None:
    from unittest.mock import AsyncMock

    import src.db.csm_set_postgres as csm_pg

    csm_pg._pool = AsyncMock(name="mock_pool")
    import asyncio

    asyncio.run(csm_pg.close_csm_set_pool())
    assert csm_pg._pool is None


def test_csm_set_close_pool_when_already_none() -> None:
    """Idempotent close — re-running close on a nulled pool is a no-op."""
    import asyncio

    import src.db.csm_set_postgres as csm_pg

    csm_pg._pool = None
    asyncio.run(csm_pg.close_csm_set_pool())
    assert csm_pg._pool is None


# --- Mongo ---


def test_mongo_client_singleton() -> None:
    from unittest.mock import patch

    with patch("src.db.mongo.AsyncIOMotorClient") as mock_client_class:
        mock_client_class.return_value = object()

        from src.db.mongo import get_client

        client1 = get_client()
        client2 = get_client()
        assert client1 is client2
        mock_client_class.assert_called_once()


def test_mongo_close_client() -> None:
    from unittest.mock import MagicMock

    import src.db.mongo as mongo

    mongo._client = MagicMock(name="mock_motor_client")
    mongo.close_client()
    assert mongo._client is None


# --- Redis ---


@pytest.mark.anyio
async def test_redis_singleton() -> None:
    from unittest.mock import MagicMock, patch

    with patch("src.db.redis_client.aioredis.from_url") as mock_from_url:
        mock_from_url.return_value = MagicMock(name="mock_redis")

        from src.db.redis_client import get_redis

        redis1 = await get_redis()
        redis2 = await get_redis()
        assert redis1 is redis2
        mock_from_url.assert_called_once()


def test_redis_close() -> None:
    from unittest.mock import AsyncMock

    import src.db.redis_client as redis_mod

    redis_mod._redis = AsyncMock(name="mock_aioredis")
    import asyncio

    asyncio.run(redis_mod.close_redis())
    assert redis_mod._redis is None
