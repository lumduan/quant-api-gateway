"""Read-only asyncpg pool for the ``db_csm_set`` database.

The gateway owns a second pool — bound to the ``CSM_SET_DSN`` env var — so
that the strategy-report endpoints can read ``trade_history`` and
``benchmark_equity_curve`` directly from ``db_csm_set`` without
round-tripping through the strategy service. The pool is configured with
the ``gateway_ro`` role (provisioned by quant-infra-db) so no write is
ever possible from this connection.

Mirrors the contract of :mod:`src.db.postgres` (eager init from the
FastAPI lifespan, lazy fallback otherwise; closed and nulled on shutdown).
"""

import asyncpg

from src.config import get_settings

_pool: asyncpg.Pool | None = None


async def get_csm_set_pool() -> asyncpg.Pool:
    """Return the lazily-initialised asyncpg pool for ``db_csm_set``.

    Returns:
        The :class:`asyncpg.Pool` bound to ``Settings.csm_set_dsn``.
    """
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(settings.csm_set_dsn)
    return _pool


async def close_csm_set_pool() -> None:
    """Close the ``db_csm_set`` pool and null out the global reference."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
