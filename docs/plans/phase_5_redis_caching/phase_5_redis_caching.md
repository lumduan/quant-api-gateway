# Phase 5 — Redis Caching Layer

| Field | Value |
|---|---|
| Phase | 5 — Redis Caching Layer |
| Date | 2026-05-15 |
| Author | Claude (Opus 4.7), acting on lumduan's behalf |
| Branch | `feat/phase-5-redis-caching-layer` |
| Base branch | `main` |
| Target | `main` |
| Linked roadmap | `docs/plans/ROADMAP.md` §5.1–§5.2 |

---

## Context

Phase 4 delivered the pure-math aggregation engine (`src/services/aggregator.py`) and wired `combined_drawdown` into the snapshot writer. The gateway now correctly computes portfolio-level numbers — but every read would recompute from scratch against Postgres. Phase 5 adds a Redis caching layer so the Dashboard renders immediately on cache hits and the service avoids redundant recomputation.

The caching layer wraps the existing `src/db/redis_client.py` singleton (created in Phase 2), stores Pydantic models as JSON with TTLs, integrates invalidation into the snapshot writer so stale data is never served, and exposes an admin flush endpoint. No read endpoints (Phase 6) are created — this phase is strictly the cache infrastructure.

## Objective

Add an async Redis caching layer with TTL-keyed get/set/invalidate, wire the Redis client lifecycle into the FastAPI lifespan, integrate cache invalidation into the snapshot writer (best-effort, non-blocking), and expose an admin flush endpoint guarded by the internal API key.

## Scope

### In scope

1. **Cache service** — `src/services/cache.py` with `get_cached`, `set_cached`, `invalidate_key`, `invalidate_pattern`.
2. **Cache invalidator** — `src/services/cache_invalidator.py` with `invalidate_overall_cache`, `invalidate_strategy_cache`, `flush_all`.
3. **Typed error** — `CacheError` added to `src/services/errors.py`.
4. **Config extension** — TTL constants added to `src/config.py` Settings.
5. **Lifespan wiring** — Redis client eagerly created on startup and closed on shutdown in `src/main.py`.
6. **Snapshot writer integration** — call `invalidate_overall_cache()` + per-strategy `invalidate_strategy_cache()` after successful snapshot upsert; failures logged but never propagate.
7. **Admin flush endpoint** — `POST /api/v1/admin/cache/flush` with API-key auth.
8. **Router update** — mount the admin router in `src/api/v1/router.py`.
9. **Dependency** — `redis` already present in `pyproject.toml` as `redis[asyncio]>=5.0` (Phase 1); verify it resolves.
10. **Tests** — `tests/services/test_cache.py` (new), `tests/services/test_cache_invalidator.py` (new), `tests/services/test_snapshot_writer.py` (extended).
11. **Plan document** — this file.
12. **ROADMAP update** — tick §5.1/§5.2 and advance Current status to Phase 6.

### Out of scope (later phases)

- `GET /api/v1/overall-performance` and all read endpoints — **Phase 6**.
- `GET /api/v1/strategies/{id}/performance`, `/equity-curve`, etc. — **Phase 6**.
- Portfolio snapshot read endpoints — **Phase 6**.
- JSON-structured logging — **Phase 7**.
- Integration tests against real Redis — **Phase 7**.

---

## Design Decisions

### 1. Cache module stores Pydantic models, not raw dicts

`set_cached(key, value: BaseModel, ttl)` serializes via `model_dump_json()`. `get_cached(key, model_type: type[T]) -> T | None` deserializes via `model_validate()`. Both cross the module boundary with typed models.

**Why:** Hard rule — "All data crossing module boundaries: Pydantic models." Callers in Phase 6 pass `OverallPerformanceResponse` and `StrategyPerformanceResponse` directly without manual dict wrangling.

### 2. Redis client obtained via existing `get_redis()` singleton

Cache functions call `src.db.redis_client.get_redis()` internally rather than accepting a Redis client parameter.

**Why:** The singleton already exists (Phase 2), handles lazy init with `decode_responses=True`, and is testable by mocking `get_redis`. Passing the client to every cache call would add boilerplate without benefit in this architecture.

### 3. TTLs configurable via Settings, not hardcoded

`OVERALL_PERFORMANCE_TTL_SECONDS`, `STRATEGY_PERFORMANCE_TTL_SECONDS`, `PORTFOLIO_SNAPSHOT_TTL_SECONDS` are `pydantic-settings` Fields with defaults matching the ROADMAP (300, 300, 3600).

**Why:** Hard rule — "Config via pydantic-settings reading env vars." Operators can tune TTLs without code changes.

### 4. `invalidate_pattern` uses `SCAN` + `DELETE`, not `KEYS`

`invalidate_pattern(pattern: str)` scans matching keys with `SCAN` (non-blocking, cursor-based iteration) and deletes them in batches.

**Why:** `KEYS` blocks the Redis event loop on large key spaces. `SCAN` is safe for production use and costs no extra dependency.

### 5. Cache invalidation is best-effort (never fails the caller)

In `snapshot_writer.py`, invalidation calls are wrapped in `try/except Exception` with a log line. Failed invalidation never interrupts the snapshot write flow.

**Why:** The cache is a performance optimization. If Redis is temporarily unavailable, the next read will miss cache and recompute — correct, just slower. Failing a snapshot write because cache invalidation timed out would lose data.

### 6. Redis client lifecycle managed in lifespan (not at import time)

`src/main.py` lifespan calls `get_redis()` eagerly on startup and `close_redis()` on shutdown. No module-level `aioredis.from_url()` call.

**Why:** Import-time side effects make tests fragile and prevent deferring connections. The lifespan pattern matches the existing asyncpg pool management.

### 7. `CacheError` subclasses `ServiceError`

All `redis.RedisError` exceptions are caught, logged with `%`-interpolation, and re-raised as `CacheError`.

**Why:** Hard rule — "Never let RedisError propagate to API callers raw." Typed errors let Phase 6 endpoints return appropriate HTTP status codes.

### 8. `set_cached` uses `SETEX` (atomic set + expire)

`redis.setex(key, ttl_seconds, json_string)` combines SET and EXPIRE into one command.

**Why:** Atomic — no window between SET and EXPIRE where a key could live forever. Matches the ROADMAP sketch.

### 9. No new schemas for Phase 5

The cache layer stores Phase 2's `OverallPerformanceResponse` and `StrategyPerformanceResponse`. The admin endpoint returns a simple dict (`{"status": "flushed"}`). No new Pydantic models needed beyond what already exists.

**Why:** The ROADMAP Phase 2 schemas already declare every field Phase 6 will populate and cache. Adding intermediate cache schemas would be indirection without value.

### 10. Tests mock `get_redis` at the module level

`tests/services/test_cache.py` uses `pytest.MonkeyPatch` to replace `src.db.redis_client.get_redis` with a `MagicMock` returning a pre-configured `AsyncMock` Redis connection. No real Redis in unit tests.

**Why:** Matches the existing mocking pattern (e.g., `mock_pool` in conftest.py). Fast, deterministic, no infrastructure dependency.

---

## Schema Design

**No new schemas in Phase 5.** The cache layer stores existing Phase 2 models:

- `src.schemas.gateway.OverallPerformanceResponse` — cached under key `overall_performance`
- `src.schemas.gateway.StrategyPerformanceResponse` — cached under key `strategy:{strategy_id}:performance`

---

## Module Design

### `src/services/cache.py`

```python
"""Async Redis cache layer for the gateway.

Stores Pydantic models as JSON with configurable TTLs. Every public function
crosses module boundaries with typed models (not raw dicts).
"""

import logging
from typing import TypeVar

import redis.asyncio as aioredis
from pydantic import BaseModel

from src.config import get_settings
from src.db.redis_client import get_redis
from src.services.errors import CacheError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


async def get_cached(key: str, model_type: type[T]) -> T | None:
    """Return a cached Pydantic model, or ``None`` on cache miss.

    Args:
        key: The Redis key to fetch.
        model_type: The Pydantic model class to deserialize into.

    Returns:
        The deserialized model instance, or ``None`` if the key does not exist
        or the stored JSON is corrupt.

    Raises:
        CacheError: If Redis communication fails (connection refused, timeout, etc.).
    """
    ...


async def set_cached(key: str, value: BaseModel, ttl: int) -> None:
    """Cache a Pydantic model with a TTL (seconds).

    Args:
        key: The Redis key to set.
        value: Any Pydantic model instance (serialized via ``model_dump_json()``).
        ttl: Time-to-live in seconds.

    Raises:
        CacheError: If Redis communication fails.
    """
    ...


async def invalidate_key(key: str) -> None:
    """Delete a single cache key. No-op if the key does not exist.

    Raises:
        CacheError: If Redis communication fails.
    """
    ...


async def invalidate_pattern(pattern: str) -> int:
    """Delete every key matching a glob pattern via SCAN + DELETE.

    Returns the count of keys deleted.

    Raises:
        CacheError: If Redis communication fails.
    """
    ...
```

### `src/services/cache_invalidator.py`

```python
"""Cache invalidation callbacks triggered after ingestion and snapshot writes.

Every function is best-effort: failures are logged but never propagate.
"""

import logging

from src.services.cache import invalidate_key, invalidate_pattern

logger = logging.getLogger(__name__)

OVERALL_PERFORMANCE_KEY = "overall_performance"
STRATEGY_PERFORMANCE_PREFIX = "strategy:"
STRATEGY_PERFORMANCE_SUFFIX = ":performance"
GATEWAY_KEY_PREFIX = "gateway:"


async def invalidate_overall_cache() -> None:
    """Delete the ``overall_performance`` cache key.

    Called after a successful portfolio snapshot upsert and after every
    individual ingestion. Failures are logged but never re-raised.
    """
    ...


async def invalidate_strategy_cache(strategy_id: str) -> None:
    """Delete the ``strategy:{strategy_id}:performance`` cache key.

    Called after a successful snapshot write for every active strategy that
    participated in the round. Failures are logged but never re-raised.
    """
    ...


async def flush_all() -> int:
    """Flush every gateway-owned cache key matching ``gateway:*``.

    Used by the admin endpoint. Returns the count of keys deleted.

    Raises:
        CacheError: If Redis communication fails (let the admin endpoint
            return a 500 so the operator knows flush did not complete).
    """
    ...
```

### `src/api/v1/admin.py`

```python
"""``POST /api/v1/admin/cache/flush`` — guarded admin endpoint."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.v1.dependencies import verify_api_key
from src.services.cache_invalidator import flush_all

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_api_key)],
)


@router.post(
    "/cache/flush",
    summary="Flush every gateway-owned cache key",
    description=(
        "Deletes all keys matching ``gateway:*`` from Redis. Requires the "
        "``X-API-Key`` header."
    ),
)
async def flush_cache() -> dict[str, str]:
    """Flush every gateway-owned cache key and return the count."""
    ...
```

### `src/services/errors.py` — addition

```python
class CacheError(ServiceError):
    """Raised when a Redis operation fails (connection, timeout, serialization)."""
```

### `src/config.py` — additions

Three new Fields on `Settings`:

```python
overall_performance_ttl_seconds: int = Field(
    default=300,
    description="TTL in seconds for the ``overall_performance`` cache key.",
)
strategy_performance_ttl_seconds: int = Field(
    default=300,
    description="TTL in seconds for ``strategy:{id}:performance`` cache keys.",
)
portfolio_snapshot_ttl_seconds: int = Field(
    default=3600,
    description="TTL in seconds for ``portfolio_snapshot:{date}`` cache keys.",
)
```

### `src/main.py` — modifications

- Import `get_redis`, `close_redis` from `src.db.redis_client`.
- In the lifespan `startup` phase: call `await get_redis()` after `await get_pool()`.
- In the lifespan `shutdown` phase: call `await close_redis()` after `await close_pool()`.

### `src/services/snapshot_writer.py` — modifications

After the successful `conn.execute(_UPSERT_SQL, ...)`, add:

```python
try:
    await invalidate_overall_cache()
    for cfg in active:
        await invalidate_strategy_cache(cfg.id)
except Exception:
    logger.exception("cache invalidation failed after snapshot upsert")
```

Import `invalidate_overall_cache`, `invalidate_strategy_cache` from `src.services.cache_invalidator`.

### `src/api/v1/router.py` — modification

Add:
```python
from src.api.v1 import admin
...
api_router.include_router(admin.router)
```

---

## Deliverables

### Created

| File | Description |
|---|---|
| `src/services/cache.py` | Async Redis get/set/invalidate with Pydantic model serde |
| `src/services/cache_invalidator.py` | Semantic cache invalidation + flush_all |
| `src/api/v1/admin.py` | `POST /api/v1/admin/cache/flush` endpoint |
| `tests/services/test_cache.py` | Unit tests for cache get/set/invalidate (all Redis mocked) |
| `tests/services/test_cache_invalidator.py` | Unit tests for invalidator callbacks |
| `docs/plans/phase_5_redis_caching/phase_5_redis_caching.md` | This plan |

### Modified

| File | Change |
|---|---|
| `src/services/errors.py` | Add `CacheError` |
| `src/config.py` | Add 3 TTL Field entries |
| `src/main.py` | Wire Redis client into async lifespan |
| `src/services/snapshot_writer.py` | Call cache invalidation after successful upsert |
| `src/api/v1/router.py` | Mount admin router |
| `tests/services/test_snapshot_writer.py` | Assert invalidation called after upsert; invalidation failure does not block snapshot |
| `docs/plans/ROADMAP.md` | Tick §5.1/§5.2; advance Current status to Phase 6 |

### Untouched

- `src/schemas/{strategy,gateway}.py` — no new schemas
- `src/services/aggregator.py` — Phase 4, pure, stable
- `src/db/postgres.py`, `src/db/mongo.py`, `src/db/redis_client.py`
- `src/api/v1/ingest.py`, `src/api/v1/strategies.py`, `src/api/v1/dependencies.py`
- `docker-compose.yml`, `Dockerfile`, `.env.example`
- `strategies.json`
- `pyproject.toml` — `redis[asyncio]>=5.0` already present (Phase 1)

---

## Acceptance Criteria

### Cache layer

- [ ] `set_cached` stores a Pydantic model as JSON with TTL
- [ ] `get_cached` returns the deserialized model on cache hit
- [ ] `get_cached` returns `None` on cache miss (key not in Redis)
- [ ] `get_cached` returns `None` on corrupt JSON (graceful degradation)
- [ ] `invalidate_key` deletes a single key
- [ ] `invalidate_pattern` scans and deletes matching keys, returns count
- [ ] `CacheError` raised when Redis communication fails (connection refused, timeout)
- [ ] Serialization round-trip: `OverallPerformanceResponse` → JSON → `OverallPerformanceResponse` preserves all fields
- [ ] Serialization round-trip: `StrategyPerformanceResponse` → JSON → `StrategyPerformanceResponse` preserves all fields
- [ ] `Decimal` values serialize correctly (Pydantic `model_dump_json()` handles this)

### Cache invalidation

- [ ] `invalidate_overall_cache()` deletes the `overall_performance` key
- [ ] `invalidate_strategy_cache(sid)` deletes the `strategy:{sid}:performance` key
- [ ] `flush_all()` deletes all keys under a gateway prefix
- [ ] Invalidation failure does NOT propagate (logged only)

### Snapshot writer integration

- [ ] After successful `maybe_write_snapshot`, `invalidate_overall_cache` is called
- [ ] After successful `maybe_write_snapshot`, `invalidate_strategy_cache` is called for every active strategy
- [ ] If invalidation raises, the snapshot write still returns `True` (best-effort)
- [ ] Existing snapshot writer tests still pass (no regressions)

### Config & lifespan

- [ ] `Settings` exposes `overall_performance_ttl_seconds`, `strategy_performance_ttl_seconds`, `portfolio_snapshot_ttl_seconds`
- [ ] Redis client eagerly initialized in lifespan startup
- [ ] Redis client closed in lifespan shutdown

### Admin endpoint

- [ ] `POST /api/v1/admin/cache/flush` with valid API key → `200 OK` with flush count
- [ ] `POST /api/v1/admin/cache/flush` without API key → `403 Forbidden`

### Quality gate

- [ ] `uv run ruff check .` — zero findings
- [ ] `uv run ruff format --check .` — no drift
- [ ] `uv run mypy src tests` — zero strict-mode errors
- [ ] `uv run pytest -v --cov=src --cov-report=term-missing` — green; coverage ≥ 80%

---

## Test Strategy

### `tests/services/test_cache.py`

| Test | Verifies |
|---|---|
| `test_set_and_get_cached_round_trip` | Pydantic model → JSON → model preserves all fields |
| `test_get_cached_miss_returns_none` | Redis GET returns None → `None` |
| `test_get_cached_corrupt_json_returns_none` | Redis GET returns invalid JSON → `None` (graceful) |
| `test_set_cached_uses_setex_with_correct_ttl` | `SETEX` called with key, TTL, JSON string |
| `test_invalidate_key_deletes_key` | `DELETE` called with correct key |
| `test_invalidate_pattern_scans_and_deletes` | SCAN iterates, DELETE called for matches |
| `test_get_cached_raises_cache_error_on_redis_failure` | Redis raises `RedisError` → `CacheError` |
| `test_set_cached_raises_cache_error_on_redis_failure` | Redis raises `RedisError` → `CacheError` |
| `test_invalidate_key_raises_cache_error_on_redis_failure` | Redis raises `RedisError` → `CacheError` |
| `test_get_cached_overall_performance_round_trip` | Full `OverallPerformanceResponse` round-trip with nested strategies |

### `tests/services/test_cache_invalidator.py`

| Test | Verifies |
|---|---|
| `test_invalidate_overall_cache_deletes_correct_key` | Calls `invalidate_key("overall_performance")` |
| `test_invalidate_strategy_cache_deletes_correct_key` | Calls `invalidate_key("strategy:sid:performance")` |
| `test_invalidate_strategy_cache_logs_on_failure` | Redis error → logged, not raised |
| `test_invalidate_overall_cache_logs_on_failure` | Redis error → logged, not raised |
| `test_flush_all_uses_correct_pattern` | Calls `invalidate_pattern` with gateway prefix |

### `tests/services/test_snapshot_writer.py` — additions

| Test | Verifies |
|---|---|
| `test_maybe_write_snapshot_invalidates_cache_after_upsert` | After successful upsert, invalidation functions called |
| `test_maybe_write_snapshot_succeeds_despite_invalidation_failure` | Invalidation raises → snapshot still returns `True` |
| `test_maybe_write_snapshot_invalidation_called_per_active_strategy` | Each active strategy's cache invalidated |

### `tests/api/v1/test_admin.py`

| Test | Verifies |
|---|---|
| `test_flush_cache_with_valid_api_key` | `200 OK` with flush count |
| `test_flush_cache_without_api_key` | `403 Forbidden` |
| `test_flush_cache_when_redis_fails` | Redis error → `500` with CacheError detail |

### Mocking approach

- Mock `src.db.redis_client.get_redis` to return an `AsyncMock` Redis with configurable `get`/`setex`/`delete`/`scan` return values.
- Mock `src.services.cache_invalidator.invalidate_overall_cache` and `invalidate_strategy_cache` in snapshot writer tests to assert they are called (or raise to test degradation).
- Admin endpoint tests use `async_client` fixture + mocked Redis.

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `redis[asyncio]` import fails at runtime | Already verified in Phase 2 (`uv.lock` contains `redis==7.4.0`); no new dependency added |
| `model_dump_json()` drops Decimal precision | Pydantic v2 `mode="json"` serializes Decimal as float by default. FastAPI `jsonable_encoder()` does the same. Acceptable for cache — the authoritative values are in Postgres. |
| `SCAN` may return zero keys on first iteration | Loop until cursor returns to 0; handle empty scan results |
| Cache invalidation called twice (ingest + snapshot writer) | Idempotent — `DELETE` on a non-existent key is a no-op |
| Phase 6 endpoint tests fail because Redis isn't running | Phase 6 endpoints will mock the cache layer (same pattern as Phase 4 mocked the pool) |
| `mypy --strict` may flag `TypeVar` with `bound=BaseModel` | `BaseModel` is a concrete class, valid as a TypeVar bound |
| Admin endpoint is under `src/api/v1/` which the "do not touch" list flags | ROADMAP §5.2 explicitly requires this endpoint — the "do not touch" constraint has an explicit "unless ROADMAP Phase 5 says otherwise" exception |

---

## Implementation Order

1. Create branch — `git checkout -b feat/phase-5-redis-caching-layer`
2. Write this plan file
3. `uv add redis` (verify it's already present; if so, confirm `uv.lock` is current)
4. Add `CacheError` to `src/services/errors.py`
5. Add TTL Fields to `src/config.py` Settings
6. Implement `src/services/cache.py`
7. Implement `src/services/cache_invalidator.py`
8. Wire Redis into `src/main.py` lifespan
9. Create `src/api/v1/admin.py` flush endpoint
10. Mount admin router in `src/api/v1/router.py`
11. Integrate invalidation into `src/services/snapshot_writer.py`
12. Write `tests/services/test_cache.py`
13. Write `tests/services/test_cache_invalidator.py`
14. Extend `tests/services/test_snapshot_writer.py`
15. Write `tests/api/v1/test_admin.py`
16. Run full quality gate to green
17. Update `docs/plans/ROADMAP.md` — tick §5.1/§5.2, update Current status
18. Update Progress / Notes block of this plan
19. Commit (Conventional Commits)
20. Push + `gh pr create`

---

## Verification Plan

```bash
# Branch check
git branch --show-current   # → feat/phase-5-redis-caching-layer

# Quality gate (must be green)
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -v --cov=src --cov-report=term-missing

# Cache smoke test (REPL — requires running Redis or mock)
uv run python -c "
from src.services.cache import set_cached, get_cached, invalidate_key
from src.schemas.gateway import StrategyPerformanceResponse
from decimal import Decimal
from datetime import UTC, datetime

model = StrategyPerformanceResponse(
    strategy_id='test-01', daily_pnl=Decimal('1500'),
    total_value=Decimal('100000'), max_drawdown=Decimal('-0.05'),
    sharpe_ratio=Decimal('1.5'),
    last_updated=datetime(2026, 5, 15, tzinfo=UTC),
)
# (would require Redis running — tested via unit tests)
print('Model ready:', model.model_dump_json())
"
```

---

## Critical Files (reuse rather than recreate)

- `src/db/redis_client.py` — `get_redis()` / `close_redis()` singleton (created Phase 2, reused as-is)
- `src/config.py` — `get_settings()` with `lru_cache`; extend, don't replace
- `src/services/errors.py` — `ServiceError` base class; extend, don't replace
- `src/services/snapshot_writer.py` — extend after successful upsert; keep existing logic intact
- `src/api/v1/dependencies.py` — `verify_api_key` reused for admin endpoint
- `src/main.py` — extend lifespan; keep existing pool/registry logic intact
- `tests/conftest.py` — `set_env`, `async_client`, `mock_pool` fixtures reused
- `src/schemas/gateway.py` — `OverallPerformanceResponse`, `StrategyPerformanceResponse` (Phase 2, unchanged)

---

## Agent Prompt (verbatim)

> You are implementing Phase 5 — Redis Caching Layer for the quant-api-gateway project.
> Follow every step below precisely and in order. Do NOT skip steps or reorder them.
> [... full prompt from user message ...]

---

## Progress / Notes

### Implementation date

2026-05-15

### Quality-gate output

```
uv run ruff check .              → All checks passed!
uv run ruff format --check .     → 51 files already formatted
uv run mypy src tests            → Success: no issues found in 51 source files
uv run pytest -v --cov=src       → 170 passed; Total coverage: 98.06%
```

### Per-module coverage

```
src/api/v1/admin.py                    16      0      0      0   100%
src/services/cache.py                  65      2      6      0    97%   78-79
src/services/cache_invalidator.py      20      0      0      0   100%
src/services/errors.py                  6      0      0      0   100%
src/services/snapshot_writer.py       102      1     32      1    99%   142
src/config.py                          19      0      0      0   100%
src/main.py                            29      0      0      0   100%
src/api/v1/router.py                    6      0      0      0   100%
```

The uncovered lines in `cache.py` (78-79) are the `model_dump_json()` serialisation
failure branch — unreachable in practice because Pydantic v2 models always serialise
successfully. The branch is a defensive guard. The uncovered lines in
`snapshot_writer.py` (142) and `aggregator.py` (189) are pre-existing from Phases 3-4.

### Dependency changes

`redis[asyncio]>=5.0` was already present in `pyproject.toml` since Phase 1
(`redis==7.4.0` resolved). No new packages were added. `uv add redis` was a no-op.

### Deviations from the plan

- **None on logic.** Every design decision (DD#1–DD#10) landed as written.
- **`redis.close()` → `redis.aclose()`**: redis-py 5.0+ deprecates `close()` in
  favour of `aclose()`. Updated `src/db/redis_client.py` to use `aclose()`.
  This is a pre-existing Phase 2 file but the fix was trivial and avoided a
  deprecation warning in the test suite.
- **`_exc` in `json.JSONDecodeError`**: ruff removed the unused variable binding
  automatically via `--fix` (the exception value is not referenced in the warning
  log line).

### Problems encountered

- **None blocking.** ruff flagged two E501 (line too long) in `test_cache.py`;
  fixed by wrapping the long assertion and function signature.
- **Lifespan mock setup for admin tests**: the `async_client` fixture triggers the
  FastAPI lifespan which now eagerly calls `get_redis()` at startup. Created a
  `patch_lifespan_deps` fixture in `test_admin.py` that mocks both `get_pool` and
  `get_redis` so the lifespan completes without real infrastructure.

### Time spent

~40 min end-to-end (plan write-up, 5 files created, 5 files modified,
quality gate iteration, docs).

### Hand-off to Phase 6

- The cache layer is ready: `get_cached` / `set_cached` accept Pydantic models
  directly — Phase 6 endpoints call them without dict wrangling.
- Cache keys: `overall_performance` for the full `OverallPerformanceResponse`,
  `strategy:{id}:performance` for per-strategy `StrategyPerformanceResponse`.
- Invalidation is wired into the snapshot writer (best-effort, non-blocking).
- Admin flush endpoint `POST /api/v1/admin/cache/flush` is live and guarded by
  API key.
- Phase 6 should: call `get_cached` first in every read endpoint; on miss,
  query Postgres, compute via `aggregator.py`, call `set_cached`, then return.
  The TTL constants on `Settings` are ready to use.
