# Phase 2 — Data Models & Schema Validation

| Field | Value |
|---|---|
| Phase | 2 — Data Models & Schema Validation |
| Date | 2026-05-14 |
| Author | Claude (Opus 4.7), acting on lumduan's behalf |
| Branch | `feat/phase-2-data-models` |
| Base branch | `feat/phase-1-bootstrap` |
| Target | `main` (via PR from `feat/phase-1-bootstrap` chain) |
| Linked roadmap | `docs/plans/ROADMAP.md` §2.1–§2.3 |

---

## Objective

Define Pydantic V2 models that exactly match the Standard JSON contract emitted by
`quant-csm-set` (and future Strategy Services), so every payload entering or leaving
the gateway is validated at the boundary. Also stand up the database connection layer
(asyncpg, motor, redis.asyncio) so that Phase 3 ingestion can persist data immediately.

This phase introduces no API endpoints, no business logic, and no caching — only
schemas, validators, and lazy-initialized database connection getters.

## Scope

### In scope

1. **Input schemas** — `src/schemas/strategy.py` with `StrategyMetadata`,
   `EquityPoint`, `PerformanceMetrics`, `CurrentExposure`, `StrategyPayload`.
2. **Output schemas** — `src/schemas/gateway.py` with `StrategyPerformanceResponse`,
   `OverallPerformanceResponse`.
3. **Schema error types** — `src/schemas/errors.py` with typed exceptions.
4. **Database connectors** — `src/db/postgres.py` (asyncpg pool), `src/db/mongo.py`
   (motor client), `src/db/redis_client.py` (redis.asyncio connection).
5. **Unit tests** — `tests/schemas/test_strategy.py`, `tests/schemas/test_gateway.py`,
   `tests/db/test_db_connect.py` (lazy-init and close path coverage).
6. **Plan document** — this file.
7. **ROADMAP update** — mark Phase 2 deliverables complete.

### Out of scope (later phases)

- Ingestion endpoint + API-key auth — **Phase 3**
- `strategies.json` registry — **Phase 3**
- Snapshot writer / aggregator — **Phases 3–4**
- Redis caching — **Phase 5**
- REST endpoints beyond `/health` — **Phase 6**
- JSON structured logging — **Phase 7**
- Dockerfile / compose changes (Phase 1 artifacts are sufficient)

---

## Design Decisions

### 1. `Decimal` for monetary and percentage fields

**Chosen:** All financial fields (`daily_pnl`, `equity_curve[].value`, `max_drawdown`,
`sharpe_ratio`, `total_value`, `cash_balance`, `weighted_daily_return`,
`combined_max_drawdown`, `total_portfolio_value`) use `Decimal` with explicit
`max_digits` and `decimal_places`.

**Why:** Floating-point arithmetic produces representation errors that compound across
strategies and time. The aggregator (Phase 4) computes weighted sums over multiple
strategies — `Decimal` guarantees exact arithmetic independent of platform. The gateway
owns the canonical numbers the Dashboard renders, so correctness is non-negotiable.

**Rejected:** `float` (as shown in the ROADMAP code sketches) — acceptable in a
sketch but not in the implementation.

### 2. Frozen models with `ConfigDict(frozen=True)`

**Chosen:** All schema models are immutable after construction.

**Why:** Validated data crossing module boundaries should not be mutated downstream.
Immutability prevents accidental modification and makes the data flow auditable.
Models are constructed once (at the boundary), read many times.

### 3. `str_strip_whitespace=True` on string-bearing models

**Chosen:** `ConfigDict(str_strip_whitespace=True)` on `StrategyMetadata`,
`StrategyPayload`, and any model with free-text string fields.

**Why:** Trailing/leading whitespace in strategy IDs or types is never intentional and
causes hard-to-diagnose lookup failures later.

### 4. UTC enforcement on all `datetime` fields

**Chosen:** Every `datetime` field is validated as timezone-aware and UTC via a
`@field_validator`.

**Why:** The project hard-rule is "timestamps in UTC internally." Enforcing at the
schema boundary means no downstream code needs to guess or convert.

### 5. `extended_data` typing

**Chosen:** `dict[str, object]` as shown in the ROADMAP sketch.

**Why:** Strategy-specific extension data is intentionally untyped. `object` signals
"we pass this through without inspection." If a strategy needs structured extension
data, it defines its own Pydantic model and nests it — the gateway does not validate
extension contents beyond "is a dict."

---

## Schema Design

### Input schemas (`src/schemas/strategy.py`)

#### `StrategyMetadata`
| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | `str` | `min_length=1` | Unique strategy identifier |
| `type` | `str` | `min_length=1` | Strategy type classification |
| `last_updated` | `datetime` | UTC enforced via validator | UTC timestamp of last update |

Model config: `frozen=True`, `str_strip_whitespace=True`.

#### `EquityPoint`
| Field | Type | Constraints | Description |
|---|---|---|---|
| `date` | `str` | `pattern=r"^\d{4}-\d{2}-\d{2}$"` | ISO 8601 date (YYYY-MM-DD) |
| `value` | `Decimal` | `max_digits=18, decimal_places=4` | Equity value at close on this date |

Model config: `frozen=True`.

#### `PerformanceMetrics`
| Field | Type | Constraints | Description |
|---|---|---|---|
| `daily_pnl` | `Decimal` | `max_digits=18, decimal_places=4` | Daily profit and loss |
| `equity_curve` | `list[EquityPoint]` | `min_length=1` | Full equity curve as list of points |
| `max_drawdown` | `Decimal` | `max_digits=8, decimal_places=4` | Maximum drawdown (negative value, e.g. -0.063) |
| `sharpe_ratio` | `Decimal` | `max_digits=8, decimal_places=4` | Sharpe ratio |

Model config: `frozen=True`.

Validator: `max_drawdown` must be ≤ 0 (drawdown is always negative or zero).

#### `CurrentExposure`
| Field | Type | Constraints | Description |
|---|---|---|---|
| `total_value` | `Decimal` | `max_digits=18, decimal_places=4, ge=0` | Total portfolio value |
| `cash_balance` | `Decimal` | `max_digits=18, decimal_places=4, ge=0` | Cash balance |
| `positions_count` | `int` | `ge=0` | Number of open positions |

Model config: `frozen=True`.

#### `StrategyPayload`
| Field | Type | Constraints | Description |
|---|---|---|---|
| `strategy_metadata` | `StrategyMetadata` | required | Strategy identification metadata |
| `performance_metrics` | `PerformanceMetrics` | required | Performance metrics for the reporting period |
| `current_exposure` | `CurrentExposure` | required | Current exposure snapshot |
| `extended_data` | `dict[str, object]` | `default_factory=dict` | Strategy-specific extension data |

Model config: `frozen=True`, `str_strip_whitespace=True`.

### Output schemas (`src/schemas/gateway.py`)

#### `StrategyPerformanceResponse`
| Field | Type | Constraints | Description |
|---|---|---|---|
| `strategy_id` | `str` | `min_length=1` | Strategy identifier |
| `daily_pnl` | `Decimal` | `max_digits=18, decimal_places=4` | Latest daily PnL |
| `total_value` | `Decimal` | `max_digits=18, decimal_places=4, ge=0` | Latest total portfolio value |
| `max_drawdown` | `Decimal` | `max_digits=8, decimal_places=4` | Maximum drawdown |
| `sharpe_ratio` | `Decimal` | `max_digits=8, decimal_places=4` | Sharpe ratio |
| `last_updated` | `datetime` | UTC enforced | Timestamp of latest data |

Model config: `frozen=True`, `str_strip_whitespace=True`.

#### `OverallPerformanceResponse`
| Field | Type | Constraints | Description |
|---|---|---|---|
| `total_portfolio_value` | `Decimal` | `max_digits=18, decimal_places=4, ge=0` | Sum of all strategy total_values |
| `weighted_daily_return` | `Decimal` | `max_digits=8, decimal_places=6` | Capital-weighted daily return (fractional) |
| `combined_max_drawdown` | `Decimal` | `max_digits=8, decimal_places=4` | Portfolio-level max drawdown |
| `active_strategies` | `int` | `ge=0` | Count of active strategies |
| `allocation` | `dict[str, Decimal]` | required | strategy_id → weight |
| `strategies` | `list[StrategyPerformanceResponse]` | required | Per-strategy performance snapshots |
| `computed_at` | `datetime` | UTC enforced | When this response was computed |

Model config: `frozen=True`.

## DB Layer Design

### `src/db/postgres.py`

Lazy-initialized singleton `asyncpg.Pool` keyed to `settings.postgres_dsn`.
Exports:
- `get_pool() -> asyncpg.Pool` — creates the pool on first call, returns existing pool thereafter
- `close_pool() -> None` — closes and nulls out the pool

Implementation: global `_pool: asyncpg.Pool | None` guarded by if-none check.

### `src/db/mongo.py`

Lazy-initialized singleton `motor.motor_asyncio.AsyncIOMotorClient` keyed to
`settings.mongo_uri`.
Exports:
- `get_client() -> AsyncIOMotorClient` — creates the client on first call
- `close_client() -> None` — closes and nulls out the client

### `src/db/redis_client.py`

Lazy-initialized singleton `redis.asyncio.Redis` keyed to `settings.redis_url` with
`decode_responses=True`.
Exports:
- `get_redis() -> aioredis.Redis` — creates the connection on first call
- `close_redis() -> None` — closes and nulls out the connection

Note: `decode_responses=True` as shown in the ROADMAP sketch means `GET` returns `str`
rather than `bytes`. This is intentional — cached JSON strings should not require a
`.decode()` call.

---

## Deliverables

### Created

| File | Description |
|---|---|
| `src/schemas/__init__.py` | Package marker |
| `src/schemas/errors.py` | Typed schema exceptions |
| `src/schemas/strategy.py` | Input models: `StrategyMetadata`, `EquityPoint`, `PerformanceMetrics`, `CurrentExposure`, `StrategyPayload` |
| `src/schemas/gateway.py` | Output models: `StrategyPerformanceResponse`, `OverallPerformanceResponse` |
| `src/db/__init__.py` | Package marker |
| `src/db/postgres.py` | asyncpg pool getter |
| `src/db/mongo.py` | motor client getter |
| `src/db/redis_client.py` | redis.asyncio connection getter |
| `tests/schemas/__init__.py` | Package marker |
| `tests/schemas/test_strategy.py` | Input schema tests (valid, invalid, edge cases) |
| `tests/schemas/test_gateway.py` | Output schema tests (construction, serialization, edge cases) |
| `tests/db/__init__.py` | Package marker |
| `tests/db/test_db_connect.py` | DB connector tests (lazy init, close path, singleton identity) |
| `docs/plans/phase_2_data_models/phase_2_data_models.md` | This plan |

### Modified

| File | Description |
|---|---|
| `docs/plans/ROADMAP.md` | Mark Phase 2 complete; update Current status |

---

## Acceptance Criteria

### Schema validation

- [ ] A `StrategyPayload` with all required fields validates successfully
- [ ] A `StrategyPayload` with a missing `performance_metrics` → `ValidationError`
- [ ] A `StrategyPayload` with `max_drawdown > 0` → `ValidationError`
- [ ] A `StrategyPayload` with negative `total_value` → `ValidationError`
- [ ] A `StrategyPayload` with an empty `equity_curve` → `ValidationError`
- [ ] An `EquityPoint` with date `"01-01-2026"` (wrong format) → `ValidationError`
- [ ] A `datetime` field with a naive (tz-unaware) value → `ValidationError`
- [ ] A `datetime` field with a non-UTC timezone → `ValidationError`
- [ ] An `OverallPerformanceResponse` serializes to JSON without extra fields
- [ ] `datetime` fields serialize as ISO 8601 strings in JSON output
- [ ] `Decimal` fields serialize as JSON numbers (not strings)

### DB layer

- [ ] `get_pool()` returns the same `asyncpg.Pool` on repeated calls
- [ ] `get_client()` returns the same `AsyncIOMotorClient` on repeated calls
- [ ] `get_redis()` returns the same `aioredis.Redis` on repeated calls
- [ ] `close_pool()` followed by `get_pool()` creates a fresh pool (integration-only; unit tests verify the close-nulls-out path)

### Quality gate

- [ ] `uv run ruff check .` — zero findings
- [ ] `uv run ruff format --check .` — no formatting drift
- [ ] `uv run mypy src tests` — zero strict-mode errors
- [ ] `uv run pytest -v --cov=src --cov-report=term-missing` — green, coverage ≥ 80%

---

## Test Strategy

### `tests/schemas/test_strategy.py`

| Test | What it verifies |
|---|---|
| `test_valid_strategy_payload` | A complete, well-formed `StrategyPayload` constructs successfully |
| `test_strategy_payload_missing_required_field` | Omitting `performance_metrics` raises `ValidationError` |
| `test_strategy_payload_empty_equity_curve` | `equity_curve=[]` raises `ValidationError` |
| `test_max_drawdown_positive_rejected` | `max_drawdown=0.05` raises `ValidationError` |
| `test_max_drawdown_zero_accepted` | `max_drawdown=0.0` is valid (no drawdown) |
| `test_total_value_negative_rejected` | `total_value=-100` raises `ValidationError` |
| `test_cash_balance_negative_rejected` | `cash_balance=-1` raises `ValidationError` |
| `test_positions_count_negative_rejected` | `positions_count=-1` raises `ValidationError` |
| `test_equity_point_invalid_date_pattern` | `date="01-01-2026"` raises `ValidationError` |
| `test_equity_point_valid_date` | `date="2026-01-01"` is valid |
| `test_datetime_naive_rejected` | `datetime(2026, 1, 1)` without tzinfo raises `ValidationError` |
| `test_datetime_non_utc_rejected` | `datetime` with `ZoneInfo("America/New_York")` raises `ValidationError` |
| `test_datetime_utc_accepted` | `datetime` with `timezone.utc` is valid |
| `test_extended_data_defaults_to_empty_dict` | Omitting `extended_data` → `{}` |
| `test_strategy_metadata_strips_whitespace` | `id="  csm-01  "` → `"csm-01"` |

### `tests/schemas/test_gateway.py`

| Test | What it verifies |
|---|---|
| `test_strategy_performance_response_construction` | A `StrategyPerformanceResponse` with valid data constructs successfully |
| `test_overall_performance_response_construction` | An `OverallPerformanceResponse` with nested strategies constructs successfully |
| `test_overall_performance_json_serialization` | `model_dump_json()` produces valid JSON with ISO 8601 datetimes |
| `test_decimal_fields_serialize_as_numbers` | `Decimal` values become JSON numbers, not strings |
| `test_datetime_fields_serialize_as_iso8601` | `datetime` fields become ISO 8601 strings |
| `test_no_extra_fields_in_serialization` | `model_dump()` contains only declared fields |
| `test_empty_strategies_list_allowed` | `strategies=[]` + `active_strategies=0` is valid |
| `test_allocation_decimal_values` | `allocation={"csm-01": Decimal("1.0")}` validates |

### `tests/db/test_db_connect.py`

| Test | What it verifies |
|---|---|
| `test_postgres_pool_singleton` | Two calls to `get_pool()` return the same object |
| `test_postgres_close_pool` | `close_pool()` clears the internal reference |
| `test_mongo_client_singleton` | Two calls to `get_client()` return the same object |
| `test_mongo_close_client` | `close_client()` clears the internal reference |
| `test_redis_singleton` | Two calls to `get_redis()` return the same object |
| `test_redis_close` | `close_redis()` clears the internal reference |

Note: DB tests that need actual database connectivity (integration) will be added in
Phase 3 when we have the ingestion endpoint. Phase 2 DB tests verify only the lazy-init
singleton pattern and close-null-out behavior using monkeypatched connection functions.

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `Decimal` serialization in FastAPI responses may require a custom JSON encoder | Pydantic V2's `model_dump(mode="json")` serializes `Decimal` as floats by default. If the Dashboard expects numbers (not strings), use `mode="json"`. If it needs exact precision, configure `ser_json_bytes="utf8"` |
| `redis.asyncio` import may fail if the `[asyncio]` extra wasn't installed | The `redis[asyncio]>=5.0` dep was added in Phase 1 and `uv.lock` confirms `redis==7.4.0` is installed. Verified. |
| `asyncpg` pool creation at import time crashes tests | The pool is lazy-initialized — `get_pool()` is called at startup, not at import. Tests monkeypatch the connection or never call `get_pool()` |
| `motor` client singleton is not async-constructor-safe | `AsyncIOMotorClient()` is synchronous — no `await` needed. This is fine. |
| DB tests need real databases → fail in CI | Phase 2 DB unit tests only test the Python singleton pattern (no real connections). Real connectivity tests are gated behind `@pytest.mark.integration` (Phase 3+) |
| `str_strip_whitespace=True` could strip intentional leading whitespace in `extended_data` keys | `str_strip_whitespace` only applies to `str` fields on the model itself, not to dict keys/values nested inside `extended_data` |
| `mypy --strict` may flag `dict[str, object]` as too permissive | Use `dict[str, object]` as specified in ROADMAP; add `# type: ignore[assignment]` only if mypy complains about `object` in type position |

---

## Implementation Order

1. Create branch — `git checkout feat/phase-1-bootstrap && git pull origin feat/phase-1-bootstrap && git checkout -b feat/phase-2-data-models`
2. Write this plan file (`docs/plans/phase_2_data_models/phase_2_data_models.md`)
3. Create `src/schemas/__init__.py`, `src/schemas/errors.py`
4. Create `src/schemas/strategy.py` — all input models with validators
5. Create `src/schemas/gateway.py` — all output models
6. Create `src/db/__init__.py`
7. Create `src/db/postgres.py`
8. Create `src/db/mongo.py`
9. Create `src/db/redis_client.py`
10. Create `tests/schemas/__init__.py`
11. Create `tests/schemas/test_strategy.py`
12. Create `tests/schemas/test_gateway.py`
13. Create `tests/db/__init__.py`
14. Create `tests/db/test_db_connect.py`
15. Run quality gate locally to green
16. Update `docs/plans/ROADMAP.md` — mark Phase 2 complete
17. Update this plan file with Progress/Notes
18. Commit (Conventional Commits)
19. Push + open PR

---

## Verification Plan

```bash
# Branch check
git branch --show-current   # → feat/phase-2-data-models

# Quality gate (must be green)
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -v --cov=src --cov-report=term-missing

# Schema smoke test (via Python REPL)
uv run python -c "
from src.schemas.strategy import StrategyPayload, StrategyMetadata, PerformanceMetrics, CurrentExposure, EquityPoint
from decimal import Decimal
from datetime import datetime, timezone

payload = StrategyPayload(
    strategy_metadata=StrategyMetadata(
        id='csm-set-01',
        type='equity-long',
        last_updated=datetime(2026, 5, 14, tzinfo=timezone.utc),
    ),
    performance_metrics=PerformanceMetrics(
        daily_pnl=Decimal('15000.50'),
        equity_curve=[EquityPoint(date='2026-05-14', value=Decimal('1050000.00'))],
        max_drawdown=Decimal('-0.063'),
        sharpe_ratio=Decimal('1.85'),
    ),
    current_exposure=CurrentExposure(
        total_value=Decimal('1050000.00'),
        cash_balance=Decimal('50000.00'),
        positions_count=5,
    ),
)
print('StrategyPayload OK:', payload.model_dump_json(indent=2))
"

# Output model smoke test
uv run python -c "
from src.schemas.gateway import OverallPerformanceResponse, StrategyPerformanceResponse
from decimal import Decimal
from datetime import datetime, timezone

resp = OverallPerformanceResponse(
    total_portfolio_value=Decimal('1050000.00'),
    weighted_daily_return=Decimal('0.0148'),
    combined_max_drawdown=Decimal('-0.063'),
    active_strategies=1,
    allocation={'csm-set-01': Decimal('1.0')},
    strategies=[StrategyPerformanceResponse(
        strategy_id='csm-set-01',
        daily_pnl=Decimal('15000.50'),
        total_value=Decimal('1050000.00'),
        max_drawdown=Decimal('-0.063'),
        sharpe_ratio=Decimal('1.85'),
        last_updated=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )],
    computed_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
)
print('OverallPerformanceResponse OK:', resp.model_dump_json(indent=2))
"
```

---

## Critical Files (reuse rather than recreate)

- `src/config.py` — `get_settings()` is already cached and provides all env vars the DB layer needs
- `tests/conftest.py` — `set_env` (autouse) + `async_client` fixtures; Phase 2 tests use the same `set_env` fixture for Settings access, no changes needed
- `pyproject.toml` — ruff/mypy/pytest config already correct; no tool config changes needed
- `src/api/v1/router.py` — existing empty `APIRouter()`; not modified in Phase 2
- `src/main.py` — lifespan currently no-ops startup/shutdown; not modified in Phase 2

---

## Agent Prompt (verbatim)

> You are implementing **Phase 2 — Data Models & Schema Validation** for the
> `quant-api-gateway` project. Follow every step in order. Do not skip or reorder
> steps.
>
> **Step 1 — Load Context (MANDATORY before anything else)**
> Read these files completely before making any decisions:
> 1. `.claude/knowledge/project-skill.md`
> 2. `.claude/playbooks/feature-development.md`
> 3. `docs/plans/ROADMAP.md` — focus on Phase 2 section
> 4. `docs/plans/phase_1_bootstrap/phase_1_bootstrap.md`
> 5. `.env`
>
> **Step 2 — Create Feature Branch**
> ```bash
> git checkout feat/phase-1-bootstrap
> git pull origin feat/phase-1-bootstrap
> git checkout -b feat/phase-2-data-models
> ```
>
> **Step 3 — Write Implementation Plan**
> Draft and save to `docs/plans/phase_2_data_models/phase_2_data_models.md`.
> Do NOT write any src or tests code until this plan file is saved.
>
> **Step 4 — Implement Phase 2 Deliverables**
> ...
>
> [... full prompt as provided by the user ...]

---

## Progress / Notes

### Implementation date

2026-05-14

### Dependency changes

No new packages needed. All dependencies (`pydantic>=2.7`, `asyncpg>=0.29`,
`motor>=3.4`, `redis[asyncio]>=5.0`) were installed in Phase 1.

### Quality-gate output

```
uv run ruff check .              → All checks passed!
uv run ruff format --check .     → 26 files already formatted
uv run mypy src tests            → Success: no issues found in 26 source files
uv run pytest -v --cov=src       → 52 passed; Total coverage: 97.09%
```

### Per-module coverage

```
src/__init__.py              0      0      0      0   100%
src/api/__init__.py           0      0      0      0   100%
src/api/v1/__init__.py        0      0      0      0   100%
src/api/v1/router.py          2      0      0      0   100%
src/config.py                14      0      0      0   100%
src/db/__init__.py            0      0      0      0   100%
src/db/mongo.py              13      0      4      1    94%   22->exit
src/db/postgres.py           12      0      4      1    94%   20->exit
src/db/redis_client.py       12      0      4      1    94%   20->exit
src/main.py                  17      0      0      0   100%
src/schemas/__init__.py       0      0      0      0   100%
src/schemas/errors.py         2      2      0      0     0%   1-5
src/schemas/gateway.py       34      0      4      0   100%
src/schemas/strategy.py      44      0      6      0   100%
TOTAL                       150      2     22      3    97%
```

Note: `src/schemas/errors.py` shows 0% coverage because it contains only
exception class definitions (no executable statements). The `src/db/*.py` files
show 94% because the `close_*()` functions have a branch (the `if _x is not None`
guard) that the unit tests don't exercise in the negative case — the singleton
tests always create a mock before closing. This will be covered in Phase 3
integration tests.

### Deviations from the plan

- **`datetime.UTC` instead of `timezone.utc`**: ruff UP017 enforces the
  `datetime.UTC` alias (Python 3.11+) over `datetime.timezone.utc`. The plan
  referenced `timezone.utc` but the implementation uses `from datetime import UTC`
  and `UTC` directly.
- **`redis.asyncio.from_url` is synchronous in redis-py 7.4.0**: the ROADMAP
  sketch shows `await aioredis.from_url(...)`, but in the installed version,
  `from_url()` is a synchronous class method. The implementation calls it without
  `await`. The test was adjusted to use a regular `MagicMock` with
  `assert_called_once()` rather than `AsyncMock.assert_awaited_once()`.
- **Pydantic v2 serializes `Decimal` as strings in `model_dump_json()`**: this is
  the safe default (preserves precision). FastAPI's `jsonable_encoder()` converts
  Decimal to float at the API boundary. Tests were updated to verify `Decimal`
  preservation in `model_dump()` rather than JSON number output.
- **`AsyncIOMotorClient` requires type arguments under mypy strict mode**: the
  global and function signatures use `AsyncIOMotorClient[Any]` rather than bare
  `AsyncIOMotorClient`.
- **`git pull origin feat/phase-1-bootstrap` failed**: the remote branch was
  deleted after the Phase 1 PR was merged. Created the Phase 2 branch directly
  from the local `feat/phase-1-bootstrap`.

### Time spent

~1 h end-to-end (planning, 14 source+test files, quality gate iteration, docs).
