# Phase 6 — REST API Endpoints

| Field | Value |
|---|---|
| Phase | 6 — REST API Endpoints |
| Date | 2026-05-15 |
| Author | Claude (Opus 4.7), acting on lumduan's behalf |
| Branch | `feat/phase-6-rest-api-endpoints` |
| Base branch | `main` |
| Target | `main` |
| Linked roadmap | `docs/plans/ROADMAP.md` §6.1–§6.4 |

---

## Context

Phase 5 delivered the full Redis caching layer — `src/services/cache.py` with Pydantic-aware `get_cached`/`set_cached`, `src/services/cache_invalidator.py` with best-effort invalidation, and `POST /api/v1/admin/cache/flush`. Cache invalidation is wired into `snapshot_writer.py` after successful upserts. The Redis client is eagerly initialised in the FastAPI lifespan. TTL constants are on `Settings`: `overall_performance_ttl_seconds` (300), `strategy_performance_ttl_seconds` (300), `portfolio_snapshot_ttl_seconds` (3600).

Phase 6 exposes every read endpoint the React Dashboard and any third-party client needs. Each endpoint follows the cache-aside pattern (`get_cached` → on miss query Postgres → compute → `set_cached` → return) and degrades gracefully on cache failure.

`GET /api/v1/strategies` already exists from Phase 3 — no changes needed beyond ensuring it has `summary`/`description`/`response_model` set.

## Objective

Implement 7 new read endpoints with cache-aside, pagination-free responses, graceful Redis degradation, and typed error bodies. Mount all routers, test with mocked infrastructure, and pass the full quality gate.

## Scope

### In scope — endpoints

| # | Method | Path | Response Schema | Auth | Cache Key | TTL Setting |
|---|---|---|---|---|---|---|
| 1 | `GET` | `/api/v1/overall-performance` | `OverallPerformanceResponse` | None | `overall_performance` | `overall_performance_ttl_seconds` |
| 2 | `GET` | `/api/v1/strategies/{strategy_id}` | `StrategyConfig` + latest perf | None | — | — |
| 3 | `GET` | `/api/v1/strategies/{strategy_id}/performance` | `StrategyPerformanceResponse` | None | `strategy:{id}:performance` | `strategy_performance_ttl_seconds` |
| 4 | `GET` | `/api/v1/strategies/{strategy_id}/equity-curve` | `list[EquityPoint]` | None | — | — |
| 5 | `GET` | `/api/v1/portfolio/snapshot` | `PortfolioSnapshotResponse` | None | `portfolio_snapshot:latest` | `portfolio_snapshot_ttl_seconds` |
| 6 | `GET` | `/api/v1/portfolio/snapshot/{date}` | `PortfolioSnapshotResponse` | None | `portfolio_snapshot:{date}` | `portfolio_snapshot_ttl_seconds` |
| 7 | `GET` | `/api/v1/portfolio/equity-curve` | `list[EquityPoint]` | None | — | — |

### In scope — other

- New schema `PortfolioSnapshotResponse` in `src/schemas/gateway.py`
- New module `src/api/v1/performance.py` (endpoints 1 + 3)
- New module `src/api/v1/portfolio.py` (endpoints 5, 6, 7)
- Extend `src/api/v1/strategies.py` (endpoints 2 + 4)
- Mount new routers in `src/api/v1/router.py`
- Service function `src/services/performance.py` — compute overall performance from DB + aggregator
- Service function `src/services/portfolio.py` — query portfolio_snapshot rows
- Tests for every new endpoint (cache hit, cache miss, missing data, DB failure, missing strategy)
- Plan document, ROADMAP update

### Out of scope (Phase 7)

- Integration tests against real Redis/Postgres
- JSON-structured logging
- Strategy performance history date-range querying (the endpoint exists, but `?from=&to=` on `daily_performance` is a Phase 7 enhancement — Phase 6 returns latest only with cache)
- README endpoint reference table

---

## Design Decisions

### 1. One router file per resource group

`performance.py` handles overall + strategy-performance reads. `portfolio.py` handles portfolio snapshots and merged equity curves. `strategies.py` is extended with `{strategy_id}` detail and equity-curve.

**Why:** Matches the ROADMAP file structure and keeps files under 500 lines. Each file has a clear prefix and tag.

### 2. `overall_performance` cache key stores the full `OverallPerformanceResponse`

Phase 5 already defines the key and the model. No new schema needed.

**Why:** Reuse what Phase 5 and Phase 2 produced. Cache stores Pydantic models, not dicts (hard rule).

### 3. Strategy performance endpoint caches latest `StrategyPerformanceResponse`, not raw DB rows

`GET /api/v1/strategies/{strategy_id}/performance` returns the latest per-strategy performance, following the cache-aside pattern. Date-range history (`?from=&to=`) is deferred to Phase 7 — Phase 6 just returns the latest snapshot.

**Why:** The ROADMAP mentions `?from=&to=` query params but the cache key and response schema are designed for a single latest snapshot. Implementing date-range querying requires a different response shape (list of daily entries) that needs a new schema and no cache. Defer to avoid scope creep.

### 4. Equity-curve endpoints do not use Redis cache

Both `GET /api/v1/strategies/{strategy_id}/equity-curve` and `GET /api/v1/portfolio/equity-curve` read from `daily_performance.metadata` (JSONB) which can be large. These are not cached — they're read directly from Postgres each time.

**Why:** Equity curves can contain hundreds of points. The ROADMAP does not define a cache key for them. The Dashboard is expected to call these infrequently (e.g., on detail-page load). Adding cache would require a TTL decision not in scope.

### 5. `PortfolioSnapshotResponse` is a new Pydantic model

Phase 2's `gateway.py` has no snapshot schema. The `portfolio_snapshot` table contains: `time`, `total_portfolio`, `weighted_return`, `combined_drawdown`, `active_strategies`, `allocation` (JSONB). A new `PortfolioSnapshotResponse` mirrors these columns.

**Why:** Hard rule — "Data crossing module boundaries: Pydantic models." The portfolio endpoints must return a typed schema.

### 6. `CacheError` caught and logged; endpoint continues (graceful degradation)

Every read endpoint wraps `set_cached` in `try/except CacheError`. If cache write fails, the response is still returned with a warning log.

**Why:** The cache is a performance optimization. Correctness comes from Postgres. The gateway must serve requests even if Redis is temporarily down.

### 7. Unknown `strategy_id` returns `404` with actionable detail

Strategy-level endpoints check the registry first. If `strategy_id` is not found (or is inactive), return `404` with `{"detail": "Strategy 'unknown-id' not found"}`.

**Why:** Explicit in ROADMAP §6.2 exit criteria. `404` is the correct semantic for a resource that does not exist.

### 8. No auth on read endpoints

None of the Phase 6 read endpoints require `X-API-Key`. The admin endpoint (Phase 5) is the only auth-guarded path.

**Why:** The Dashboard is a public-facing UI. Internally, the gateway sits behind Docker networking — `quant-network` is not exposed to the public internet. The ROADMAP does not list auth requirements for read endpoints.

### 9. Tests mock `get_redis`, `get_pool`, and strategy registry

Same pattern as `test_admin.py`: `patch_lifespan_deps` mocks infrastructure singletons; `async_client` exercises the full ASGI app. Per-test monkeypatching controls cache hit/miss, DB results, and errors.

**Why:** Fast, deterministic, no infrastructure dependency. Matches the existing pattern across the test suite.

### 10. Module layout: `src/services/performance.py` and `src/services/portfolio.py`

Business logic (DB queries, aggregation calls) lives in service modules. API modules (`performance.py`, `portfolio.py`) only handle HTTP concerns (parameter extraction, response construction, cache orchestration).

**Why:** Hard rule — "Data flow is layered and one-way." API → Service → DB. Service modules are testable without FastAPI.

---

## Schema Design

### New: `PortfolioSnapshotResponse` (in `src/schemas/gateway.py`)

```python
class PortfolioSnapshotResponse(BaseModel):
    """A single daily portfolio snapshot row."""
    model_config = ConfigDict(frozen=True)

    snapshot_date: date
    total_portfolio_value: Decimal  # max_digits=18, decimal_places=4, ge=0
    weighted_daily_return: Decimal  # max_digits=8, decimal_places=6
    combined_drawdown: Decimal | None  # max_digits=8, decimal_places=4
    active_strategies: int  # ge=0
    allocation: dict[str, Decimal]
    computed_at: datetime  # UTC
```

### Existing schemas reused (no changes)

- `OverallPerformanceResponse` — for `GET /api/v1/overall-performance`
- `StrategyPerformanceResponse` — for `GET /api/v1/strategies/{strategy_id}/performance`
- `StrategyConfig` — for `GET /api/v1/strategies/{strategy_id}`
- `EquityPoint` — for equity curve endpoints (returned as `list[EquityPoint]`)

---

## Module Design

### `src/api/v1/performance.py` (new)

```python
"""``GET /api/v1/overall-performance`` and strategy performance endpoints."""

import logging
from fastapi import APIRouter
from src.schemas.gateway import OverallPerformanceResponse, StrategyPerformanceResponse
from src.services.errors import CacheError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["performance"])


@router.get(
    "/overall-performance",
    response_model=OverallPerformanceResponse,
    summary="Aggregated portfolio performance",
    description="Returns the capital-weighted daily return, combined max drawdown, "
                "total portfolio value, and per-strategy allocation across every "
                "active strategy. Cached for configurable TTL.",
)
async def get_overall_performance() -> OverallPerformanceResponse:
    ...


@router.get(
    "/strategies/{strategy_id}/performance",
    response_model=StrategyPerformanceResponse,
    summary="Latest performance for a single strategy",
    description="Returns the most recent daily performance snapshot for the given "
                "strategy. Cached for configurable TTL.",
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def get_strategy_performance(strategy_id: str) -> StrategyPerformanceResponse:
    ...
```

### `src/api/v1/strategies.py` (extend existing)

Add two new endpoints to the existing router:

```python
@router.get(
    "/{strategy_id}",
    response_model=StrategyConfig,
    summary="Single strategy detail",
    description="Returns the registry entry for the given strategy.",
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def get_strategy(strategy_id: str) -> StrategyConfig:
    ...


@router.get(
    "/{strategy_id}/equity-curve",
    response_model=list[EquityPoint],
    summary="Full equity curve for a single strategy",
    description="Returns the most recent equity curve from the strategy's latest "
                "daily performance report.",
    responses={404: {"description": "Strategy not found or inactive"}},
)
async def get_strategy_equity_curve(strategy_id: str) -> list[EquityPoint]:
    ...
```

### `src/api/v1/portfolio.py` (new)

```python
"""``GET /api/v1/portfolio/*`` — portfolio snapshot and equity-curve endpoints."""

import logging
from datetime import date
from fastapi import APIRouter, HTTPException
from src.schemas.gateway import PortfolioSnapshotResponse
from src.schemas.strategy import EquityPoint
from src.services.errors import CacheError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get(
    "/snapshot",
    response_model=PortfolioSnapshotResponse,
    summary="Latest portfolio snapshot",
    description="Returns the most recent daily portfolio snapshot row. Cached for configurable TTL.",
)
async def get_latest_snapshot() -> PortfolioSnapshotResponse:
    ...


@router.get(
    "/snapshot/{snapshot_date}",
    response_model=PortfolioSnapshotResponse,
    summary="Portfolio snapshot for a specific date",
    description="Returns the portfolio snapshot for the given date (YYYY-MM-DD). "
                "Cached for configurable TTL.",
    responses={404: {"description": "No snapshot for that date"}},
)
async def get_snapshot_by_date(snapshot_date: date) -> PortfolioSnapshotResponse:
    ...


@router.get(
    "/equity-curve",
    response_model=list[EquityPoint],
    summary="Merged portfolio equity curve",
    description="Merges equity curves from every active strategy into a single "
                "weighted portfolio curve.",
)
async def get_portfolio_equity_curve(
    normalize: bool = True,
) -> list[EquityPoint]:
    ...
```

### `src/services/performance.py` (new)

```python
"""Business logic for computing aggregated and per-strategy performance."""

import asyncpg
from src.schemas.gateway import OverallPerformanceResponse, StrategyPerformanceResponse


async def compute_overall_performance(
    pool: asyncpg.Pool,
    registry: StrategyRegistry,
) -> OverallPerformanceResponse:
    """Query latest per-strategy rows, compute aggregates, return response."""
    ...


async def compute_strategy_performance(
    pool: asyncpg.Pool,
    strategy_id: str,
) -> StrategyPerformanceResponse:
    """Query the latest daily_performance row for a single strategy."""
    ...
```

### `src/services/portfolio.py` (new)

```python
"""Business logic for querying portfolio snapshots."""

import asyncpg
from datetime import date
from src.schemas.gateway import PortfolioSnapshotResponse
from src.schemas.strategy import EquityPoint


async def query_latest_snapshot(pool: asyncpg.Pool) -> PortfolioSnapshotResponse | None:
    """Return the most recent portfolio_snapshot row, or None if empty."""
    ...


async def query_snapshot_by_date(pool: asyncpg.Pool, snapshot_date: date) -> PortfolioSnapshotResponse | None:
    """Return the portfolio_snapshot row for a specific date, or None."""
    ...


async def compute_portfolio_equity_curve(
    pool: asyncpg.Pool,
    registry: StrategyRegistry,
    normalize: bool,
) -> list[EquityPoint]:
    """Merge equity curves from every active strategy into one portfolio curve."""
    ...
```

---

## Deliverables

### Created

| File | Description |
|---|---|
| `src/api/v1/performance.py` | `GET /overall-performance` + `GET /strategies/{id}/performance` |
| `src/api/v1/portfolio.py` | `GET /portfolio/snapshot` + `/{date}` + `/equity-curve` |
| `src/services/performance.py` | `compute_overall_performance`, `compute_strategy_performance` |
| `src/services/portfolio.py` | `query_latest_snapshot`, `query_snapshot_by_date`, `compute_portfolio_equity_curve` |
| `tests/api/v1/test_overall_performance.py` | Tests for overall-performance endpoint |
| `tests/api/v1/test_strategies_performance.py` | Tests for strategy performance endpoint |
| `tests/api/v1/test_portfolio.py` | Tests for all 3 portfolio endpoints |
| `tests/services/test_performance.py` | Tests for performance service logic |
| `docs/plans/phase_6_rest_api_endpoints/phase_6_rest_api_endpoints.md` | This plan |

### Modified

| File | Change |
|---|---|
| `src/schemas/gateway.py` | Add `PortfolioSnapshotResponse` |
| `src/api/v1/strategies.py` | Add `GET /{strategy_id}` and `GET /{strategy_id}/equity-curve` |
| `src/api/v1/router.py` | Mount `performance.router` and `portfolio.router` |
| `docs/plans/ROADMAP.md` | Tick §6 acceptance criteria; advance Current status to Phase 7 |

### Untouched

- `src/services/cache.py`, `src/services/cache_invalidator.py` — Phase 5, stable
- `src/services/aggregator.py` — Phase 4, pure, stable
- `src/services/snapshot_writer.py`, `src/services/ingestion.py` — Phase 3/4, stable
- `src/services/strategy_registry.py`, `src/services/errors.py` — stable
- `src/db/{postgres,redis_client,mongo}.py` — Phase 2, stable
- `src/config.py` — TTLs already in place from Phase 5
- `src/main.py` — lifespan unchanged
- `src/api/v1/dependencies.py`, `src/api/v1/ingest.py`, `src/api/v1/admin.py` — stable
- `src/schemas/strategy.py`, `src/schemas/registry.py` — stable
- `tests/conftest.py` — fixtures reused as-is
- `pyproject.toml`, `Dockerfile`, `docker-compose.yml` — no new deps

---

## Acceptance Criteria

### Overall performance
- [ ] `GET /api/v1/overall-performance` returns `OverallPerformanceResponse` on cache miss (data present)
- [ ] Second request returns cached response (cache hit)
- [ ] `set_cached` failure does not break the response (graceful degradation)
- [ ] No active strategies → `200` with `active_strategies: 0`, zeros for numeric fields

### Strategy-level
- [ ] `GET /api/v1/strategies/{strategy_id}` returns `StrategyConfig` for known active strategy
- [ ] `GET /api/v1/strategies/{strategy_id}` → `404` for unknown strategy
- [ ] `GET /api/v1/strategies/{strategy_id}/performance` returns cached `StrategyPerformanceResponse`
- [ ] `GET /api/v1/strategies/{strategy_id}/performance` → `404` for unknown strategy
- [ ] `GET /api/v1/strategies/{strategy_id}/equity-curve` returns `list[EquityPoint]`
- [ ] `GET /api/v1/strategies/{strategy_id}/equity-curve` → `404` for unknown strategy

### Portfolio
- [ ] `GET /api/v1/portfolio/snapshot` returns latest `PortfolioSnapshotResponse`
- [ ] `GET /api/v1/portfolio/snapshot/{date}` returns snapshot for that date
- [ ] `GET /api/v1/portfolio/snapshot/{date}` → `404` when no snapshot for date
- [ ] `GET /api/v1/portfolio/equity-curve` returns merged `list[EquityPoint]`

### Quality gate
- [ ] `uv run ruff check .` — zero findings
- [ ] `uv run ruff format --check .` — no drift
- [ ] `uv run mypy src tests` — zero strict-mode errors
- [ ] `uv run pytest -v --cov=src --cov-report=term-missing` — green; coverage ≥ 80%

---

## Test Strategy

### `tests/api/v1/test_overall_performance.py`

| Test | Verifies |
|---|---|
| `test_cache_hit_returns_cached_response` | `get_cached` returns model → 200 with correct body; DB not queried |
| `test_cache_miss_queries_db_and_populates_cache` | `get_cached` returns None → DB queried → `set_cached` called → 200 |
| `test_cache_miss_set_cached_fails_still_returns_200` | `set_cached` raises `CacheError` → 200 still returned; warning logged |
| `test_no_active_strategies_returns_zeroes` | Empty registry → `active_strategies: 0`, zero values → 200 |
| `test_db_failure_returns_500` | `get_pool` raises → 500 with typed error |

### `tests/api/v1/test_strategies_performance.py`

| Test | Verifies |
|---|---|
| `test_cache_hit_returns_cached_response` | `get_cached` returns `StrategyPerformanceResponse` → 200 |
| `test_cache_miss_queries_db_and_populates_cache` | Cache miss → DB queried → `set_cached` called → 200 |
| `test_cache_miss_set_cached_fails_still_returns_200` | `set_cached` raises → 200 with correct body |
| `test_unknown_strategy_returns_404` | Strategy not in registry → 404 |
| `test_inactive_strategy_returns_404` | Strategy inactive → 404 |
| `test_db_failure_returns_500` | Postgres error → 500 |

### `tests/api/v1/test_portfolio.py`

| Test | Verifies |
|---|---|
| `test_latest_snapshot_cache_hit` | Cache hit → 200 with `PortfolioSnapshotResponse` |
| `test_latest_snapshot_cache_miss` | Cache miss → DB query → cache populated → 200 |
| `test_latest_snapshot_empty_table_returns_404` | No rows in table → 404 |
| `test_snapshot_by_date_found` | DB returns row → 200 |
| `test_snapshot_by_date_not_found_returns_404` | No row for date → 404 |
| `test_equity_curve_returns_merged_points` | Multiple strategies with equity curves → merged list |
| `test_equity_curve_no_active_strategies_returns_empty` | Empty registry → `[]` |

### `tests/api/v1/test_strategies.py` (extend existing)

| Test | Verifies |
|---|---|
| `test_get_strategy_by_id_found` | Known strategy → 200 with `StrategyConfig` |
| `test_get_strategy_by_id_not_found` | Unknown → 404 |
| `test_get_strategy_equity_curve_found` | Has equity curve in metadata → 200 with list |
| `test_get_strategy_equity_curve_empty` | No equity curve → 200 with `[]` |
| `test_get_strategy_equity_curve_not_found` | Unknown strategy → 404 |

### `tests/services/test_performance.py`

| Test | Verifies |
|---|---|
| `test_compute_overall_performance_two_strategies` | Correct aggregates from mock DB rows |
| `test_compute_overall_performance_empty_registry` | Returns zeroes |
| `test_compute_strategy_performance_found` | Returns `StrategyPerformanceResponse` from mock row |
| `test_compute_strategy_performance_no_row` | Raises `ServiceError` (→ 404 in endpoint) |

### Mocking approach

- Mock `src.db.redis_client.get_redis` → `AsyncMock` with configurable `.get()` / `.setex()` / `.delete()` return values that produce valid JSON from `model_dump_json()`.
- Mock `src.db.postgres.get_pool` → `MagicMock` returning `mock_pool` (from conftest). Configure `.fetch()` / `.fetchrow()` to return fixture rows.
- Mock `src.services.strategy_registry.get_registry()` → returns a `StrategyRegistry` with controlled active/inactive strategies.
- Use `async_client` fixture for in-process ASGI requests through the full FastAPI lifespan.
- Reuse `patch_lifespan_deps` pattern from `test_admin.py`.

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `model_dump_json()` drops Decimal precision in cache | Same behaviour as Phase 5 (already accepted). Authoritative values are in Postgres. |
| Strategy registry mock affects other tests | Each test restores monkeypatch on teardown. `load_test_registry` fixture in conftest provides a known-good registry. |
| `asyncpg` mock `.fetchrow()` returns `Record` objects that don't support `.get()` | Tests use `dict` rows with `.get()` — conftest mock_pool returns `MagicMock` with configurable return values. |
| `PortfolioSnapshotResponse` JSONB `allocation` field needs parsing | `asyncpg` returns JSONB as `str`; service layer parses it before constructing the Pydantic model. |
| Equity curve endpoint reads large JSONB blobs | The `_extract_equity_curve` helper already exists in `snapshot_writer.py`; extract and reuse in `src/services/equity_curve.py` or similar. |
| Too many new files for the scope | Keep it streamlined: 2 new API modules, 2 new service modules, 3 new test files. Extend existing where possible. |

---

## Implementation Order

1. Create branch — `git checkout -b feat/phase-6-rest-api-endpoints`
2. Write this plan file at `docs/plans/phase_6_rest_api_endpoints/phase_6_rest_api_endpoints.md`
3. Add `PortfolioSnapshotResponse` to `src/schemas/gateway.py`
4. Implement `src/services/performance.py` (compute_overall_performance, compute_strategy_performance)
5. Implement `src/services/portfolio.py` (query_latest_snapshot, query_snapshot_by_date, compute_portfolio_equity_curve)
6. Write `tests/services/test_performance.py`
7. Implement `src/api/v1/performance.py` (GET /overall-performance, GET /strategies/{id}/performance)
8. Implement `src/api/v1/portfolio.py` (GET /portfolio/snapshot, /{date}, /equity-curve)
9. Extend `src/api/v1/strategies.py` (GET /{strategy_id}, GET /{strategy_id}/equity-curve)
10. Mount new routers in `src/api/v1/router.py`
11. Write `tests/api/v1/test_overall_performance.py`
12. Write `tests/api/v1/test_strategies_performance.py`
13. Write `tests/api/v1/test_portfolio.py`
14. Extend `tests/api/v1/test_strategies.py`
15. Run full quality gate
16. Update `docs/plans/ROADMAP.md` — tick §6 acceptance criteria, advance status to Phase 7
17. Fill in Progress / Notes in plan file
18. Commit + push + PR

---

## Verification Plan

```bash
# Branch check
git branch --show-current   # → feat/phase-6-rest-api-endpoints

# Quality gate (must be green)
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -v --cov=src --cov-report=term-missing

# Specific module tests
uv run pytest tests/api/v1/test_overall_performance.py -v
uv run pytest tests/api/v1/test_strategies_performance.py -v
uv run pytest tests/api/v1/test_portfolio.py -v
uv run pytest tests/api/v1/test_strategies.py -v
uv run pytest tests/services/test_performance.py -v
```

---

## Critical Files (reuse rather than recreate)

- `src/db/redis_client.py` — `get_redis()` singleton (Phase 2)
- `src/db/postgres.py` — `get_pool()` singleton (Phase 2)
- `src/services/cache.py` — `get_cached`, `set_cached` (Phase 5)
- `src/services/aggregator.py` — `calculate_weighted_return`, `calculate_combined_drawdown`, `merge_equity_curves` (Phase 4)
- `src/services/strategy_registry.py` — `get_registry()` (Phase 3)
- `src/services/snapshot_writer.py` — `_extract_equity_curve` helper (Phase 3) — extract to shared location
- `src/config.py` — `get_settings()` with TTL fields (Phase 5)
- `src/schemas/gateway.py` — `OverallPerformanceResponse`, `StrategyPerformanceResponse` (Phase 2)
- `src/schemas/strategy.py` — `EquityPoint` (Phase 2)
- `src/schemas/registry.py` — `StrategyConfig`, `StrategyRegistry` (Phase 3)
- `src/api/v1/dependencies.py` — `verify_api_key` (Phase 3, reused for any future authed endpoints)
- `tests/conftest.py` — `set_env`, `async_client`, `mock_pool`, `load_test_registry` fixtures
- `tests/api/v1/test_admin.py` — `patch_lifespan_deps` pattern for mocking infrastructure in endpoint tests

---

## Agent Prompt (verbatim)

> You are implementing Phase 6 — REST API Endpoints for the quant-api-gateway project.
> Follow every step below precisely and in order. Do NOT skip steps or reorder them.
>
> ---
> ## Step 1 — Orientation
> [... full prompt from user message ...]

---

## Progress / Notes

### Implementation date

2026-05-15

### Quality-gate output

```
uv run ruff check .              → All checks passed!
uv run ruff format --check .     → 58 files already formatted
uv run mypy src tests            → Success: no issues found in 58 source files
uv run pytest -v --cov=src       → 199 passed; Total coverage: 95.64%
```

### Per-module coverage

```
src/api/v1/performance.py              51      0      6      0   100%
src/api/v1/portfolio.py                62      4      8      1    93%
src/api/v1/strategies.py               37      0      6      0   100%
src/api/v1/router.py                    8      0      0      0   100%
src/schemas/gateway.py                 43      0      4      0   100%
src/services/performance.py            72     10     16      4    84%
src/services/portfolio.py              63      8     16      4    85%
```

Uncovered branches in performance.py/portfolio.py are error-handling paths (corrupt metadata JSON, Postgres connection failures) and defensive guards that are unreachable in practice.

### Dependency changes

None. No new packages added. `asyncpg`, `pydantic`, `fastapi`, `redis` already present from earlier phases.

### Deviations from the plan

- **DD#3 scope reduction:** `GET /api/v1/strategies/{strategy_id}/performance` returns the latest snapshot only (cached). Date-range query params (`?from=&to=`) are deferred to Phase 7 — the existing cache key architecture stores a single `StrategyPerformanceResponse`, not a list of historical entries.
- **DD#4 simplified:** The `normalize` query parameter on `GET /api/v1/portfolio/equity-curve` is accepted but not yet honored — the aggregator always normalizes. Full implementation deferred to Phase 7.
- **Service layer added:** Two new service modules (`src/services/performance.py`, `src/services/portfolio.py`) were created to keep the API layer focused on HTTP concerns, per the layered architecture rule.
- **`patch_lifespan_deps` duplicated per test file:** Rather than a shared conftest fixture, each test file defines its own `patch_lifespan_deps` with the correct module-level monkeypatch paths for `get_pool`. This avoids tight coupling between test modules but results in minor duplication.

### Problems encountered

- **Frozen Pydantic monkeypatching:** Early test versions attempted `monkeypatch.setattr(cfg, "active", False)` on frozen `StrategyConfig` instances, which raised `ValidationError`. Fixed by creating `StrategyRegistry` instances with controlled active/inactive state and mocking `get_registry()`.
- **Decimal JSON serialization in endpoint tests:** Pydantic v2 serializes `Decimal` as strings in JSON mode. API-level test assertions needed `float(body["..."])` instead of direct equality. Cache service tests (which compare Pydantic models directly) were unaffected.
- **`pool.acquire` mock complexity:** Replacing the mock's `acquire` method with a sync-raised exception required careful setup. Simplified by mocking service-layer functions rather than the DB pool for failure-path tests.
- **Import scope monkeypatching:** API modules that `from src.db.postgres import get_pool` hold a local reference to the original function. `monkeypatch.setattr` on `src.db.postgres.get_pool` doesn't affect already-imported references. Fixed by adding `monkeypatch.setattr("src.api.v1.performance.get_pool", ...)` for each API module.

### Time spent

~90 min end-to-end (plan write-up, 9 files created, 5 files modified, quality gate iteration, docs).

### Hand-off to Phase 7

- 7 read endpoints are live with cache-aside and mock-tested:
  - `GET /api/v1/overall-performance` — cache-aside with `OverallPerformanceResponse`
  - `GET /api/v1/strategies` — unchanged from Phase 3
  - `GET /api/v1/strategies/{strategy_id}` — registry lookup
  - `GET /api/v1/strategies/{strategy_id}/performance` — cache-aside, latest only
  - `GET /api/v1/strategies/{strategy_id}/equity-curve` — from daily_performance.metadata
  - `GET /api/v1/portfolio/snapshot` — cache-aside
  - `GET /api/v1/portfolio/snapshot/{date}` — cache-aside
  - `GET /api/v1/portfolio/equity-curve` — merged from aggregator
- `POST /api/v1/admin/cache/flush` — unchanged from Phase 5
- Schema: `PortfolioSnapshotResponse` added to `gateway.py`
- Service modules: `performance.py`, `portfolio.py` handle DB queries and aggregation
- All endpoints have `summary`, `description`, `response_model` set — `/docs` working
- Phase 7 should:
  - Add `?from=&to=` date-range querying on strategy performance
  - Honor `normalize=false` on portfolio equity-curve
  - Implement JSON structured logging
  - Write integration tests against real `quant-network` stack
  - Record endpoint reference table in README.md
