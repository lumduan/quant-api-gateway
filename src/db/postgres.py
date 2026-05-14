import asyncpg

from src.config import get_settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the lazily-initialized asyncpg pool for ``db_gateway``."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(settings.postgres_dsn)
    return _pool


async def close_pool() -> None:
    """Close the asyncpg pool and null out the global reference."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
