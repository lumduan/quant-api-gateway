# Phase 3 — Strategy Ingestion & Data Storage

| Field | Value |
|---|---|
| Phase | 3 — Strategy Ingestion & Data Storage |
| Date | 2026-05-14 |
| Author | Claude (Opus 4.7), acting on lumduan's behalf |
| Branch | `feat/phase-3-strategy-ingestion` |
| Base branch | `main` |
| Target | `main` |
| Linked roadmap | `docs/plans/ROADMAP.md` §3.1–§3.3 |

---

## Objective

`quant-api-gateway` has finished Phase 1 (FastAPI bootstrap + Docker Compose) and Phase 2
(Pydantic schemas + DB connection layer). It can validate `StrategyPayload` and open lazy
connections to PostgreSQL, MongoDB, and Redis — but it cannot yet **ingest** anything.

Phase 3 closes that gap. After this phase, every Strategy Service (today: `quant-csm-set`)
can POST a Daily Performance report to the gateway, the gateway authenticates the call,
validates the payload, persists a row to `db_gateway.daily_performance`, and — once every
active strategy has reported for the day — writes an aggregate row to
`db_gateway.portfolio_snapshot`. A `GET /api/v1/strategies` endpoint exposes the registry
so the Dashboard / ops tooling can discover what's active without code changes.

This phase deliberately stops short of cache, the full aggregation math (Phase 4), and any
Dashboard-facing read endpoints (Phase 6). It is purely write-path + registry + a single
read endpoint to expose the registry.

The DB schema is owned by `quant-infra-db` (sibling repo) and is **not** modified here.
Confirmed table shapes:

```sql
-- db_gateway.daily_performance
time TIMESTAMPTZ NOT NULL, strategy_id TEXT NOT NULL,
daily_return / cumulative_return / total_value / cash_balance /
max_drawdown / sharpe_ratio DOUBLE PRECISION, metadata JSONB
UNIQUE (time, strategy_id)

-- db_gateway.portfolio_snapshot
time TIMESTAMPTZ NOT NULL, total_portfolio DOUBLE PRECISION NOT NULL,
weighted_return / combined_drawdown DOUBLE PRECISION,
active_strategies INTEGER, allocation JSONB
UNIQUE (time)
```

## Scope

### In scope

1. **Ingestion endpoint** — `POST /api/v1/ingest/daily-report` accepts a
   `StrategyPayload`, authenticates via `X-API-Key`, persists a `daily_performance` row
   (upsert on `(time, strategy_id)`), and triggers the snapshot writer.
2. **API-key dependency** — `verify_api_key` FastAPI dependency that compares
   `X-API-Key` against `Settings.internal_api_key` using `secrets.compare_digest`.
3. **Strategy registry** — `strategies.json` at repo root, loaded once on lifespan
   startup, exposed via a getter. `Settings.strategy_registry_path` (default
   `Path("strategies.json")`) lets tests/Docker override.
4. **`GET /api/v1/strategies`** — returns every active registry entry.
5. **Daily performance writer** — `src/services/ingestion.py` with a single async
   function that maps `StrategyPayload` → INSERT params and runs the upsert.
6. **Snapshot writer** — `src/services/snapshot_writer.py` triggered after each
   successful ingest; if every active strategy in the registry has reported for today
   (UTC date), it computes (`total_portfolio`, `weighted_return`, `active_strategies`,
   `allocation`) and upserts a `portfolio_snapshot` row for that day.
7. **Lifespan wiring** — `src/main.py` lifespan loads the registry on startup, opens
   the asyncpg pool eagerly, and closes both on shutdown.
8. **Typed errors** — `src/services/errors.py` (root `ServiceError`, plus
   `StrategyRegistryLoadError`, `UnknownStrategyError`, `IngestionPersistError`).
9. **Tests** — unit tests for registry loading, payload→row mapping, snapshot
   aggregation, API-key dep; ASGI-layer tests (using `httpx.AsyncClient` +
   `ASGITransport`, with the asyncpg pool mocked) for the new endpoints.
10. **Plan document** — this file.
11. **Roadmap update** — mark Phase 3 §3.1, §3.2, §3.3 boxes complete.

### Out of scope (later phases)

- Combined drawdown / equity-curve merger — **Phase 4** (`combined_drawdown` left NULL
  in Phase 3).
- Redis caching of aggregated reads — **Phase 5**.
- Cache invalidation hooks on ingest — **Phase 5**.
- Dashboard-facing read endpoints (`/overall-performance`, `/strategies/{id}/performance`,
  `/portfolio/...`) — **Phase 6**.
- JSON structured logging — **Phase 7** (Phase 3 uses stdlib `logging` with the
  `%`-format rule).
- Real database integration tests — **Phase 7** (Phase 3 mocks the asyncpg pool).

---

## Design Decisions

### 1. `daily_pnl` → `daily_return` mapping

**Chosen:** `daily_return = float(daily_pnl) / float(total_value)` when `total_value > 0`,
else `0.0`. Raw `daily_pnl` is preserved inside the `metadata` JSONB so nothing is lost.

**Why:** Matches the Phase 4 aggregator formula
`(daily_pnl_i / total_value_i) × weight_i`. Storing the raw PnL in `metadata` keeps the
column NULL-free **and** lets Phase 4 still recover the original value if needed.

### 2. `cumulative_return` computed from `equity_curve`

**Chosen:** When `len(equity_curve) ≥ 2`, `cumulative_return = float(last.value /
first.value) - 1.0`. With exactly one point, store `NULL`.

**Why:** Cheap to compute at ingest time, captures useful info immediately, avoids
re-reading the curve later. NULL when only one point is honest.

### 3. Snapshot writer triggered inline, only when round is complete

**Chosen:** After each successful ingest, the writer:

1. Loads the active strategy ids from the registry.
2. Queries `daily_performance` for the latest row per active strategy whose
   `time::date = today (UTC)`.
3. If every active strategy has reported today, computes aggregates and upserts a
   `portfolio_snapshot` row keyed by `today` at `00:00 UTC`.
4. Otherwise returns silently (no snapshot written).

**Why:** Matches ROADMAP §3.3 verbatim ("after a full ingestion round"). Inline keeps
Phase 3 self-contained — no scheduler / cron needed.

### 4. Repository layer under `src/services/`, not a new `src/data/`

**Chosen:** SQL-bearing functions live in `src/services/ingestion.py` and
`src/services/snapshot_writer.py`, alongside the registry. They consume the asyncpg pool
from `src/db/postgres.py`.

**Why:** ROADMAP file structure explicitly lists `services/snapshot_writer.py` and
`services/strategy_registry.py`. Introducing `src/data/` for one phase would diverge from
the documented structure.

### 5. Strategy registry at repo root as `strategies.json`, path is configurable

**Chosen:** Default `Settings.strategy_registry_path: Path = Path("strategies.json")`.
The registry is loaded eagerly on lifespan startup — failure aborts startup with
`StrategyRegistryLoadError`. Loaded data lives in a module-global guarded by a getter
matching the Phase 2 `get_pool()` pattern.

**Why:** ROADMAP §3.2 says "no code change required" to add a strategy. Eager startup
fail-fast surfaces problems in Docker instead of at first ingest.

### 6. Pydantic models for the registry, not raw dicts

**Chosen:** `StrategyConfig` + `StrategyRegistry` Pydantic models in
`src/schemas/registry.py`. The loader parses JSON and returns a frozen
`StrategyRegistry`.

**Why:** Hard rule — "Pydantic at boundaries." Bonus: `model_dump()` gives the
`GET /api/v1/strategies` response for free.

### 7. Decimal → float conversion at the SQL boundary, not in schemas

**Chosen:** `StrategyPayload` keeps Phase 2's `Decimal` typing. The mapping function
in `src/services/ingestion.py` converts via explicit `float(d)` when building INSERT
params, since `daily_performance` columns are `DOUBLE PRECISION`.

**Why:** Keeps Phase 2's exact-arithmetic contract for in-memory math; concedes float at
the storage boundary because the DB column is float anyway. Conversion is local.

### 8. Upsert via `INSERT ... ON CONFLICT (...) DO UPDATE`

**Chosen:** All `daily_performance` writes are upserts on
`uq_daily_perf_time_strategy`. Same for `portfolio_snapshot` on
`uq_portfolio_snapshot_time`.

**Why:** Idempotent ingest. A strategy resending the same `last_updated` overwrites
instead of failing. Snapshot writer recomputes today's row each time the round closes.

### 9. UTC date-bucketing for the snapshot

**Chosen:** "Today" = `datetime.now(UTC).date()`. The snapshot row's `time` column is
`datetime.combine(today, time.min, tzinfo=UTC)` (midnight UTC of today).

**Why:** Hard rule — UTC internally. One snapshot row per UTC day matches the ROADMAP
example.

### 10. No HTTP retries / circuit breakers in Phase 3

**Chosen:** Phase 3 owns only the **inbound** ingestion path — no outbound HTTP. We do
not introduce `httpx.AsyncClient` here.

**Why:** YAGNI. Outbound calls don't exist in Phase 3.

---

## Schema Design

### `src/schemas/registry.py`

#### `StrategyConfig`

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | `str` | `min_length=1`, whitespace-stripped | Unique strategy identifier |
| `name` | `str` | `min_length=1`, whitespace-stripped | Human-readable strategy name |
| `service_url` | `str` | `min_length=1` | Base URL of the Strategy Service |
| `capital_weight` | `Decimal` | `ge=0, max_digits=8, decimal_places=4` | Allocation weight |
| `active` | `bool` | default `True` | Whether the strategy is included in the round |

Model config: `frozen=True`, `str_strip_whitespace=True`.

#### `StrategyRegistry`

| Field | Type | Constraints | Description |
|---|---|---|---|
| `strategies` | `list[StrategyConfig]` | required | All strategies (active or not) |

Methods:

- `active_strategies() -> list[StrategyConfig]` — filter `active=True`.
- `by_id(strategy_id) -> StrategyConfig | None` — lookup by id.

Model config: `frozen=True`.

---

## Module Design

### `src/services/errors.py`

```python
class ServiceError(Exception): ...
class StrategyRegistryLoadError(ServiceError): ...
class UnknownStrategyError(ServiceError): ...
class IngestionPersistError(ServiceError): ...
```

### `src/services/strategy_registry.py`

- `_registry: StrategyRegistry | None` module global.
- `load_registry(path: Path) -> StrategyRegistry` — read JSON, validate, raise
  `StrategyRegistryLoadError` on `FileNotFoundError`, `JSONDecodeError`,
  `ValidationError`.
- `set_registry(reg)`, `clear_registry()`, `get_registry() -> StrategyRegistry` —
  matching the Phase 2 `get_pool()` pattern. `get_registry()` raises
  `StrategyRegistryLoadError` if the registry hasn't been set yet.

### `src/services/ingestion.py`

- `async def persist_daily_report(payload: StrategyPayload, *, pool: asyncpg.Pool) -> None`
- `_payload_to_row(payload) -> dict[str, Any]` — pure mapping function
  - `time` ← `payload.strategy_metadata.last_updated`
  - `strategy_id` ← `payload.strategy_metadata.id`
  - `daily_return` ← `float(daily_pnl)/float(total_value)` if `total_value > 0` else `0.0`
  - `cumulative_return` ← derived when `len(equity_curve) ≥ 2`, else `None`
  - `total_value`, `cash_balance`, `max_drawdown`, `sharpe_ratio` ← `float(...)`
  - `metadata` ← `json.dumps(...)` with Decimal→str (lossless preservation of
    `daily_pnl`, `equity_curve`, `extended_data`, `positions_count`, `type`).
- `_UPSERT_SQL` constant at module top.
- `asyncpg.PostgresError` is wrapped as `IngestionPersistError`.

### `src/services/snapshot_writer.py`

- `async def maybe_write_snapshot(*, pool, registry, now=None) -> bool`
  - Default `now = datetime.now(UTC)`; date bucket is `now.date()`.
  - SQL: `SELECT DISTINCT ON (strategy_id) strategy_id, total_value, daily_return,
    max_drawdown FROM daily_performance WHERE time::date = $1 AND strategy_id = ANY($2)
    ORDER BY strategy_id, time DESC`.
  - If returned rows < active count → return `False`.
  - Else compute aggregates via `_compute_aggregates` and upsert.
  - Returns `True` on snapshot written.
- `_compute_aggregates(rows, active) -> SnapshotAggregates` — pure dataclass-returning
  helper, easily unit-tested.

### `src/api/v1/dependencies.py`

```python
async def verify_api_key(
    key: str | None = Depends(_api_key_header),
) -> None:
    expected = get_settings().internal_api_key
    provided = key or ""
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
```

`secrets.compare_digest` for constant-time comparison.

### `src/api/v1/ingest.py`

- `router = APIRouter(prefix="/ingest", tags=["ingest"], dependencies=[Depends(verify_api_key)])`
- `POST /daily-report`: 201 on success; 404 on unknown strategy id; persist daily row,
  then best-effort snapshot trigger (logged + swallowed if it fails).

### `src/api/v1/strategies.py`

- `router = APIRouter(prefix="/strategies", tags=["strategies"])`
- `GET /` → `list[StrategyConfig]` of every active strategy.

### `src/api/v1/router.py`

```python
api_router = APIRouter()
api_router.include_router(ingest.router)
api_router.include_router(strategies.router)
```

### `src/main.py`

Lifespan startup: load registry from `Settings.strategy_registry_path`; call
`get_pool()` eagerly. Shutdown: `close_pool()`, `clear_registry()`.

### `src/config.py`

Add `strategy_registry_path: Path = Field(default=Path("strategies.json"), ...)`.

### `strategies.json` (repo root)

```json
{
  "strategies": [
    {
      "id": "csm-set-01",
      "name": "CSM SET Strategy",
      "service_url": "http://quant-csm-set:8001",
      "capital_weight": 1.0,
      "active": true
    }
  ]
}
```

### `Dockerfile` (modify — one line)

Add `COPY strategies.json ./` so the registry ships into the container image.

---

## Deliverables

### Created

| File | Description |
|---|---|
| `src/schemas/registry.py` | `StrategyConfig`, `StrategyRegistry` Pydantic models |
| `src/services/__init__.py` | package marker |
| `src/services/errors.py` | typed service exceptions |
| `src/services/strategy_registry.py` | JSON loader + module-global getter |
| `src/services/ingestion.py` | `StrategyPayload` → `daily_performance` upsert |
| `src/services/snapshot_writer.py` | `portfolio_snapshot` writer (round-complete trigger) |
| `src/api/v1/dependencies.py` | `verify_api_key` dependency |
| `src/api/v1/ingest.py` | `POST /api/v1/ingest/daily-report` |
| `src/api/v1/strategies.py` | `GET /api/v1/strategies` |
| `strategies.json` | initial registry (csm-set-01 only) |
| `tests/services/__init__.py` | package marker |
| `tests/services/test_strategy_registry.py` | loader + validation + missing/malformed file |
| `tests/services/test_ingestion.py` | mapping + pool-mocked persist path |
| `tests/services/test_snapshot_writer.py` | aggregate math + round-complete gating |
| `tests/api/v1/test_dependencies.py` | api-key positive/negative |
| `tests/api/v1/test_ingest.py` | 201, 403, 422, 404 paths |
| `tests/api/v1/test_strategies.py` | returns active strategies |
| `tests/schemas/test_registry.py` | registry schema edge cases |
| `tests/strategies.fixture.json` | 2-strategy + 1-inactive fixture for tests |
| `docs/plans/phase_3_strategy_ingestion/phase_3_strategy_ingestion.md` | this plan |

### Modified

| File | Change |
|---|---|
| `src/api/v1/router.py` | mount `ingest` + `strategies` sub-routers |
| `src/main.py` | lifespan loads registry + opens pool, closes on shutdown |
| `src/config.py` | add `strategy_registry_path` setting |
| `tests/conftest.py` | add `STRATEGY_REGISTRY_PATH` to `_TEST_ENV`; reset registry/pool fixtures |
| `Dockerfile` | `COPY strategies.json ./` |
| `.env.example` | document optional `STRATEGY_REGISTRY_PATH` |
| `docs/plans/ROADMAP.md` | tick §3.1/§3.2/§3.3; update Current status |

### Untouched

- `src/db/{postgres,mongo,redis_client}.py` (Phase 2 — reused)
- `src/schemas/{strategy,gateway,errors}.py` (Phase 2 — reused as-is)
- `pyproject.toml` / `uv.lock` (no new deps)
- `docker-compose.yml` (Compose stack unchanged)

---

## Acceptance Criteria

### Endpoint behaviour

- [x] `POST /api/v1/ingest/daily-report` with valid `StrategyPayload` and correct
      `X-API-Key` → `201 Created` (2026-05-14)
- [x] Same call upserts a row in `daily_performance` keyed by `(last_updated,
      strategy_id)` — verified via mocked-pool assertion (`ON CONFLICT (time,
      strategy_id) DO UPDATE`) in `tests/services/test_ingestion.py`
- [x] Missing `X-API-Key` → `403 Forbidden`
- [x] Wrong `X-API-Key` value → `403 Forbidden`
- [x] Body missing required field → `422 Unprocessable Entity` (Pydantic detail)
- [x] `strategy_metadata.id` not in registry → `404 Not Found`, no DB write
- [x] `GET /api/v1/strategies` returns the active registry entries as JSON

### Registry

- [x] `strategies.json` parses on startup; malformed file aborts startup with
      `StrategyRegistryLoadError`
- [x] Adding a strategy to `strategies.json` and restarting the container makes it
      visible in `GET /api/v1/strategies` — no code change
- [x] `Settings.strategy_registry_path` is honoured (the conftest test fixture path is
      consumed by `load_test_registry`)

### Snapshot writer

- [x] When only some active strategies have reported today → no `portfolio_snapshot` row
- [x] When every active strategy has reported → exactly one snapshot row exists for
      today (UTC midnight)
- [x] Snapshot has `total_portfolio = Σ total_value`, `active_strategies = len(active)`,
      `allocation` = normalised weights, `weighted_return` per the documented formula
- [x] Snapshot writer raising → ingest still returns `201` (best-effort hook)

### Mapping correctness

- [x] `_payload_to_row` maps `daily_pnl=15000.50, total_value=1050000.00` →
      `daily_return ≈ 0.014286…`
- [x] `_payload_to_row` with single-point `equity_curve` → `cumulative_return is None`
- [x] `_payload_to_row` with `[100.00, 110.00]` curve → `cumulative_return ≈ 0.10`
- [x] `metadata` JSON round-trip preserves `daily_pnl`, `equity_curve`, `extended_data`,
      `positions_count`, `type`

### Quality gate

- [x] `uv run ruff check .` — zero findings
- [x] `uv run ruff format --check .` — no drift (43 files already formatted)
- [x] `uv run mypy src tests` — zero strict-mode errors (43 source files)
- [x] `uv run pytest -v --cov=src --cov-report=term-missing` — green, coverage 98.25%
      (target ≥ 80%; 102 passed)

---

## Test Strategy

### `tests/schemas/test_registry.py`

| Test | Verifies |
|---|---|
| `test_strategy_config_valid` | Happy-path construction |
| `test_strategy_config_negative_weight_rejected` | `capital_weight=-0.1` → `ValidationError` |
| `test_strategy_config_id_strips_whitespace` | `id="  csm-01  "` → `"csm-01"` |
| `test_strategy_config_active_defaults_true` | Omitting `active` → `True` |
| `test_strategy_registry_active_strategies_filter` | Only `active=True` returned |
| `test_strategy_registry_by_id_lookup` | Found / not-found paths |

### `tests/services/test_strategy_registry.py`

| Test | Verifies |
|---|---|
| `test_load_registry_happy_path` | tmp file → parsed registry |
| `test_load_registry_missing_file` | raises `StrategyRegistryLoadError` |
| `test_load_registry_invalid_json` | raises `StrategyRegistryLoadError` |
| `test_load_registry_validation_error` | raises `StrategyRegistryLoadError` |
| `test_get_registry_unset_raises` | `get_registry()` before set raises |
| `test_set_and_clear_roundtrip` | set → get → clear → get raises |

### `tests/services/test_ingestion.py`

| Test | Verifies |
|---|---|
| `test_payload_to_row_basic_fields` | `time/strategy_id/...` mapping |
| `test_payload_to_row_daily_return_formula` | `daily_pnl/total_value` math |
| `test_payload_to_row_cumulative_return_two_points` | `(last/first)-1` |
| `test_payload_to_row_cumulative_return_one_point` | `None` |
| `test_payload_to_row_metadata_round_trip` | JSONB preserves everything |
| `test_payload_to_row_total_value_zero` | `daily_return = 0.0` |
| `test_persist_daily_report_executes_upsert_sql` | mocked pool — execute called |
| `test_persist_daily_report_wraps_postgres_error` | `PostgresError` → `IngestionPersistError` |

### `tests/services/test_snapshot_writer.py`

| Test | Verifies |
|---|---|
| `test_compute_aggregates_two_strategies` | weighted_return + allocation math |
| `test_compute_aggregates_zero_total_weight` | no div-by-zero |
| `test_compute_aggregates_normalised_allocation` | weights sum to 1.0 |
| `test_maybe_write_snapshot_round_incomplete` | 1/2 rows → `False`, no upsert |
| `test_maybe_write_snapshot_round_complete` | 2/2 → `True`, upsert called |

### `tests/api/v1/test_dependencies.py`

| Test | Verifies |
|---|---|
| `test_verify_api_key_valid` | matching key passes |
| `test_verify_api_key_missing` | None → 403 |
| `test_verify_api_key_wrong` | wrong value → 403 |

### `tests/api/v1/test_ingest.py`

| Test | Verifies |
|---|---|
| `test_ingest_happy_path` | mocked pool + registry → 201 |
| `test_ingest_missing_api_key` | 403 |
| `test_ingest_invalid_body` | 422 |
| `test_ingest_unknown_strategy` | 404 |
| `test_ingest_snapshot_writer_failure_does_not_break_ingest` | snapshot raises → still 201 |

### `tests/api/v1/test_strategies.py`

| Test | Verifies |
|---|---|
| `test_list_strategies_returns_active_only` | filters out `active=false` |

### Mocking approach

- `asyncpg.Pool` mocked via `unittest.mock.AsyncMock` — `pool.acquire()` returns an
  `AsyncMock` connection whose `execute` / `fetch` methods are `AsyncMock`s.
- `get_pool()` monkeypatched per test.
- `set_registry()` called explicitly per test from `tests/strategies.fixture.json`.

### Integration tests

Deferred to Phase 7 (gated by `pytest -m integration`).

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `daily_pnl/total_value` is the wrong "daily return" for some strategies | Raw `daily_pnl` is preserved in `metadata` JSONB; Phase 4 can re-derive any other definition |
| `secrets.compare_digest` trips on `None` | Normalise a missing header to `""` before calling |
| Snapshot writer fires twice if a strategy posts twice on the same day | `ON CONFLICT (time) DO UPDATE` makes the second write idempotent |
| Strategy posts with a prior-day `last_updated` could spuriously close today's round | Snapshot logic uses `datetime.now(UTC).date()`, not `payload.last_updated.date()` |
| Lifespan crash on missing `strategies.json` brings the container down on startup | Intended — fail fast |
| Eager `get_pool()` on startup fails when Postgres is down | Acceptable: gateway is useless without Postgres |
| `metadata` JSONB with `Decimal` values fails `json.dumps` by default | `default=str` keeps lossless precision |
| `ASGITransport` doesn't run lifespan → registry / pool not loaded in tests | Tests call `set_registry(...)` and patch `get_pool` directly |
| `mypy --strict` flags `dict[str, Any]` for the JSONB blob | Documented in the helper's docstring — JSONB schema is untyped by intent |
| `pytest --cov-fail-under=80` will fail mid-phase | Implement source files + tests in pairs |

---

## Implementation Order

1. Create branch — `git checkout -b feat/phase-3-strategy-ingestion`
2. Run baseline gate — must be green before any code is written
3. Write this plan file
4. Registry schemas — `src/schemas/registry.py` + tests
5. Registry service — `src/services/{__init__,errors,strategy_registry}.py` + tests
6. Settings update — add `strategy_registry_path`; extend conftest
7. Ingestion service — `src/services/ingestion.py` + tests
8. Snapshot writer — `src/services/snapshot_writer.py` + tests
9. API-key dependency — `src/api/v1/dependencies.py` + tests
10. Ingest router — `src/api/v1/ingest.py` + tests
11. Strategies router — `src/api/v1/strategies.py` + tests
12. Mount routers — modify `src/api/v1/router.py`
13. Lifespan wiring — modify `src/main.py`; update `tests/test_main.py`
14. Dockerfile + `.env.example` — `COPY strategies.json ./` + env var note
15. Quality gate to green
16. Update this plan file + ROADMAP
17. Commit + push + open PR

---

## Verification Plan

```bash
# Branch
git branch --show-current   # → feat/phase-3-strategy-ingestion

# Quality gate
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -v --cov=src --cov-report=term-missing

# Manual smoke (with .env loaded)
uv run uvicorn src.main:app --port 8000 &
sleep 2

curl -s localhost:8000/health
curl -s localhost:8000/api/v1/strategies | jq

curl -i -X POST localhost:8000/api/v1/ingest/daily-report \
  -H "X-API-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_metadata": {"id":"csm-set-01","type":"equity-long","last_updated":"2026-05-14T11:00:00Z"},
    "performance_metrics": {
      "daily_pnl":"15000.50",
      "equity_curve":[{"date":"2026-05-13","value":"1035000.00"},{"date":"2026-05-14","value":"1050000.00"}],
      "max_drawdown":"-0.063",
      "sharpe_ratio":"1.85"
    },
    "current_exposure": {"total_value":"1050000.00","cash_balance":"50000.00","positions_count":5}
  }'

curl -i -X POST localhost:8000/api/v1/ingest/daily-report -d '{}'   # → 403

# DB-side verification (against db_gateway from quant-network)
docker exec -it quant-postgres psql -U postgres -d db_gateway \
  -c "SELECT time, strategy_id, daily_return, total_value FROM daily_performance ORDER BY time DESC LIMIT 5;"

docker exec -it quant-postgres psql -U postgres -d db_gateway \
  -c "SELECT time, total_portfolio, active_strategies, allocation FROM portfolio_snapshot ORDER BY time DESC LIMIT 5;"
```

---

## Critical Files (reuse rather than recreate)

- `src/db/postgres.py` — `get_pool()` / `close_pool()` (Phase 2)
- `src/schemas/strategy.py` — `StrategyPayload` and nested models (Phase 2)
- `src/config.py` — `get_settings()` already cached (Phase 1)
- `tests/conftest.py` — `set_env` + `async_client` fixtures (Phase 1)
- `src/api/v1/router.py` — empty `APIRouter()` mount point (Phase 1)
- `pyproject.toml` — ruff/mypy/pytest config is correct; **no new dependencies in Phase 3**

---

## Agent Prompt (verbatim)

> You are implementing **Phase 3 — Strategy Ingestion & Data Storage** for the
> `quant-api-gateway` project. Follow every step below in strict order. Do not skip
> steps or reorder them.
>
> **Step 1 — Load Project Knowledge**
> Read these files completely before doing anything else: `.claude/knowledge/project-skill.md`,
> `.claude/playbooks/feature-development.md`, `.claude/knowledge/architecture.md`,
> `.claude/knowledge/coding-standards.md`.
>
> **Step 2 — Read Phase Context**
> Read `docs/plans/ROADMAP.md` (focus on Phase 3 — Strategy Ingestion & Data Storage) and
> `docs/plans/phase_2_data_models/phase_2_data_models.md` (reference format for the Phase 3
> plan document).
>
> **Step 3 — Create Git Branch** — `git checkout -b feat/phase-3-strategy-ingestion`.
>
> **Step 4 — Run Baseline Quality Gate** — note pre-existing failures so you don't
> accidentally own them.
>
> **Step 5 — Draft Implementation Plan** — create
> `docs/plans/phase_3_strategy_ingestion/phase_3_strategy_ingestion.md`. The plan must
> include: Overview, Scope, Deliverables, Architecture decisions, Implementation steps,
> Acceptance criteria, Risks & mitigations, Test strategy, the full agent prompt.
>
> **Step 6 — Implement Phase 3** — apply hard rules: typed signatures, Pydantic at
> boundaries, async/await + `httpx.AsyncClient` with `timeout=` for all I/O, stdlib
> `logging` with `%`-formatting, config via `pydantic-settings`, typed errors per
> subpackage `errors.py`, UTC timestamps, ≤500 LOC per file, sorted/no-wildcard imports.
>
> **Step 7 — Write Tests** — unit tests (no network, mock externals); integration tests
> gated behind `@pytest.mark.integration`; cover happy path, validation errors, retry
> exhaustion, edge cases; ≥80% coverage.
>
> **Step 8 — Run Full Quality Gate** — fix all errors before proceeding.
>
> **Step 9 — Update Documentation** — tick acceptance criteria in
> `docs/plans/phase_3_strategy_ingestion/phase_3_strategy_ingestion.md`; mark Phase 3
> complete in `docs/plans/ROADMAP.md`.
>
> **Step 10 — Commit and Open PR** — Conventional Commits; push; `gh pr create` to `main`.

---

## Progress / Notes

### Implementation date

2026-05-14

### Quality-gate output

```
uv run ruff check .              → All checks passed!
uv run ruff format --check .     → 43 files already formatted
uv run mypy src tests            → Success: no issues found in 43 source files
uv run pytest -v --cov=src       → 102 passed; Total coverage: 98.25%
```

### Per-module coverage

```
src/api/v1/dependencies.py        100%
src/api/v1/ingest.py              100%
src/api/v1/router.py              100%
src/api/v1/strategies.py          100%
src/config.py                     100%
src/main.py                       100%
src/schemas/registry.py           100%
src/services/__init__.py          100%
src/services/errors.py            100%
src/services/ingestion.py          95% (one error-path log unreached)
src/services/snapshot_writer.py   100%
src/services/strategy_registry.py 100%
```

Pre-existing 94% coverage on `src/db/{postgres,mongo,redis_client}.py` is unchanged —
the missing branch is the negative `if _pool is not None` case, exercised only by
integration tests (Phase 7).

### Deviations from the plan

- **No changes**. Every decision in §"Design Decisions" landed as written.
- A stale-venv shebang from a previous repo location required `rm -rf .venv && uv sync
  --all-groups` before tooling worked. Not a code change — just a one-time environment
  reset.
- The original test plan included `test_list_strategies_500_when_registry_unset`. It
  was removed because `httpx.ASGITransport` re-raises in-app exceptions instead of
  converting to a `500` response — the test was asserting harness behaviour, not the
  app's. The registry-unset path is unreachable in production (lifespan guarantees the
  registry is loaded before the first request).
- `tests/test_main.py::test_lifespan_runs_startup_and_shutdown` was extended to set
  the asyncpg `_pool` global to a mock and assert that the registry is loaded /
  cleared by the lifespan.
- A small `_decimal_to_str` helper in `src/services/ingestion.py` handles the
  `Decimal → str` conversion inside the `metadata` JSONB blob. This keeps the JSONB
  contents lossless (Phase 4 can still recover the original `Decimal`).

### Time spent

~1 h end-to-end (planning, 18 new files, quality-gate iteration, docs).

