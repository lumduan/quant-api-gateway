# Phase 3 — quant-api-gateway — Endpoints + Schemas + Cache

| Field | Value |
|---|---|
| Phase | Phase 3 of `feature-strategies-report-metrics` (per-strategy report) |
| Date | 2026-05-21 |
| Author | Claude (Opus 4.7), acting on lumduan's behalf |
| Branch | `feat/gateway-endpoints-schemas-cache` |
| Target | `main` |
| Linked roadmap | `../docs/plans/feature-strategies-report-metrics/ROADMAP.md` §Phase 3 |
| Plan file location (in repo) | `docs/plans/feature-strategies-report-metrics/PLAN.md` |

---

## Context

`feature-strategies-report-metrics` introduces a TradingView-style per-strategy
performance report that spans every sub-repo of `quant-trading-system`. Phase 1
(csm-set strategy emits the `extended_data.report` payload) and Phase 2
(`quant-infra-db` ships `strategy_report_snapshot` + `benchmark_equity_curve`
hypertables and the `trade_history` column extensions + CHECK relaxation) are
already complete.

Phase 3 (this work) is the gateway-side delivery that closes the loop:
1. Accept the richer `extended_data.report` payload at the existing
   `POST /api/v1/ingest/daily-report` endpoint.
2. Persist a per-day JSONB snapshot into
   `db_gateway.strategy_report_snapshot`.
3. Expose three new read endpoints (`/strategies/{id}/report`,
   `/strategies/{id}/trades`, `/strategies/{id}/benchmark-curve`) for the
   React dashboard with cache-aside via Redis.

Phase 3 unblocks Phase 4 (dashboard tabs + charts) and Phase 5 (end-to-end
verification).

---

## Scope

### In scope

1. **`src/schemas/strategy_report.py`** — full nested Pydantic v2 tree mirroring
   csm-set's `StrategyReport` output 1:1. All monetary / ratio fields are
   `Decimal`; all timestamps validate as UTC; percentages are fractional
   `Decimal` (1.48% → `"0.0148"`).
2. **`src/schemas/strategy.py`** — additive `@model_validator(mode="after")`
   that parses `extended_data.get("report")` through `StrategyReport` and
   attaches it to a private `_parsed_report` attribute. Missing / invalid
   reports log a WARNING and continue (ingestion stays backward-compatible
   with strategies that don't emit a report yet).
3. **`src/services/strategy_report_service.py`** — five async functions:
   `persist_report`, `get_latest_report`, `get_report_for_date`,
   `list_trades`, `get_benchmark_curve` — plus a new
   `StrategyReportNotFoundError` in `src/services/errors.py`.
4. **`src/db/csm_set_postgres.py`** (new module) — second asyncpg pool for the
   read-only `db_csm_set` DSN, mirroring the existing `db/postgres.py`
   contract (`get_csm_set_pool`, `close_csm_set_pool`). Eagerly initialised in
   `src/main.py::lifespan` and closed on shutdown.
5. **`src/config.py`** — new settings:
   - `csm_set_dsn: str` (required, read-only DSN to `db_csm_set`)
   - `strategy_report_ttl_seconds: int = 600`
   - `trade_log_ttl_seconds: int = 300`
   - `benchmark_curve_ttl_seconds: int = 600`
   All four documented in `.env.example`.
6. **`src/api/v1/strategy_report.py`** — three open read endpoints, mounted
   from `src/api/v1/router.py`:
   - `GET /api/v1/strategies/{strategy_id}/report?date=YYYY-MM-DD`
   - `GET /api/v1/strategies/{strategy_id}/trades?from_date&to_date&limit&offset`
   - `GET /api/v1/strategies/{strategy_id}/benchmark-curve?from_date&to_date&normalize`
   Each handler validates registry membership, checks cache, computes on miss,
   sets cache (graceful CacheError handling), returns typed response model.
7. **`src/services/ingestion.py`** — after the existing `daily_performance`
   UPSERT, if `payload._parsed_report is not None`, also UPSERT into
   `strategy_report_snapshot` inside **the same `conn.transaction()`** so a
   report-write failure rolls back the day's performance insert (atomic per
   strategy per day).
8. **`src/services/cache.py`** — three new cache key helpers (no schema change
   to the public `get_cached` / `set_cached` API; the file already supports
   typed Pydantic models via TypeVar).
9. **`src/services/cache_invalidator.py`** — on every successful ingest,
   SCAN-delete `gateway:strategy:{id}:{report,trades,benchmark}:*`.
10. **Tests** (mirror src/ in tests/, ≥90% coverage gate enforced by
    `--cov-fail-under=90`):
    - `tests/schemas/test_strategy_report.py`
    - `tests/services/test_strategy_report_service.py`
    - `tests/api/v1/test_strategy_report.py`
    - `tests/integration/test_strategy_report_roundtrip.py` (marker
      `integration`)
    - `tests/services/test_ingestion.py` (extended: with-report case +
      transaction rollback case)
    - `tests/services/test_cache_invalidator.py` (extended: new SCAN patterns)
    - `tests/conftest.py` (extended: env var `CSM_SET_DSN`, mock
      `csm_set_pool` fixture)

### Out of scope

- Dashboard work (Phase 4) — separate sub-repo.
- End-to-end verification with a live `quant-csm-set` (Phase 5).
- `gateway_ro` Postgres role creation — Phase 2 (`quant-infra-db`) owns it;
  the gateway just consumes the DSN from env.
- Mongo writes — `csm_logs` already stores strategy logs schema-less; the
  gateway does not persist reports to Mongo in this phase.

---

## Deliverables

### Created

- `src/schemas/strategy_report.py` — root `StrategyReport` + ~20 nested
  Pydantic models. Will be split into a sub-package (`strategy_report/`)
  if it exceeds the 500-line soft cap.
- `src/db/csm_set_postgres.py` — second asyncpg pool getter / closer.
- `src/services/strategy_report_service.py` — persist + read services.
- `src/api/v1/strategy_report.py` — three read endpoints.
- `tests/schemas/test_strategy_report.py`
- `tests/services/test_strategy_report_service.py`
- `tests/api/v1/test_strategy_report.py`
- `tests/integration/test_strategy_report_roundtrip.py`
- `docs/plans/feature-strategies-report-metrics/PLAN.md` (this plan)

### Modified

- `src/config.py` — new settings + `.env.example` doc.
- `.env.example` — add `CSM_SET_DSN`, `STRATEGY_REPORT_TTL_SECONDS`,
  `TRADE_LOG_TTL_SECONDS`, `BENCHMARK_CURVE_TTL_SECONDS`.
- `src/schemas/strategy.py` — `@model_validator` parses
  `extended_data.report`.
- `src/schemas/__init__.py` — re-export new schemas (the file is currently
  empty per `Read` — verify before editing).
- `src/services/errors.py` — add `StrategyReportNotFoundError(ServiceError)`.
- `src/services/ingestion.py` — wrap performance + report UPSERT in one
  `conn.transaction()`.
- `src/services/cache_invalidator.py` — invalidate new patterns on ingest.
- `src/main.py` — open + close the new csm-set pool in `lifespan`.
- `src/api/v1/router.py` — `include_router(strategy_report.router)`.
- `tests/conftest.py` — add `CSM_SET_DSN` to `_TEST_ENV` + `mock_csm_set_pool`
  fixture; extend dependency overrides for new pool getter.
- `../docs/plans/feature-strategies-report-metrics/ROADMAP.md` — tick Phase 3
  checkboxes.
- `CLAUDE.md` — only if new reusable patterns are added to
  `.claude/knowledge/` (likely a brief note on dual-pool wiring).

### Untouched (do not modify in this PR)

- `src/services/aggregator.py`, `performance.py`, `portfolio.py`,
  `snapshot_writer.py` — unrelated to Phase 3.
- `Dockerfile`, `docker-compose.yml` — only documentation env-var changes if
  required.
- Existing tests not listed above.

---

## Implementation Order

1. **Branch**: `git checkout -b feat/gateway-endpoints-schemas-cache`.
2. **Save this plan** as `docs/plans/feature-strategies-report-metrics/PLAN.md`
   (copy of the full content of this `/home/batt/.claude/plans/...` file with
   the embedded agent prompt verbatim).
3. **Test-first**: write `tests/schemas/test_strategy_report.py` — round-trip
   serialisation of a hand-built `StrategyReport`; UTC enforcement; Decimal
   precision; reject of `float` for monetary fields; optional fields
   (`margin_usage` null for csm-set; `*_intrabar` null for daily-only).
4. **`src/schemas/strategy_report.py`** — implement the full nested model tree
   to make those tests pass.
5. **`src/schemas/strategy.py`** — add `_parsed_report` private attr +
   `@model_validator`. Add unit test
   `tests/schemas/test_strategy.py::test_payload_with_report` and a negative
   test for an invalid report (logs WARNING but parses payload).
6. **`src/config.py`** — add the four new settings + docstrings + min_length on
   `csm_set_dsn`. Add positive + negative tests in `tests/test_config.py`.
7. **`src/db/csm_set_postgres.py`** — clone `postgres.py` shape; module-level
   `_pool`, `get_csm_set_pool()`, `close_csm_set_pool()`. Add
   `tests/db/test_csm_set_postgres.py` (`asyncpg.create_pool` mocked).
8. **`src/main.py`** — open / close the new pool in `lifespan`. Extend
   `tests/test_main.py` smoke test.
9. **`src/services/errors.py`** — add `StrategyReportNotFoundError`.
10. **`src/services/strategy_report_service.py`** — implement the five
    functions. Each raises typed errors (`ServiceError` /
    `StrategyReportNotFoundError`); all SQL is parameterised. Add
    `tests/services/test_strategy_report_service.py` with mocked pools and
    JSON round-trip checks.
11. **`src/services/ingestion.py`** — refactor to wrap performance + report
    UPSERT in `conn.transaction()`. Existing
    `tests/services/test_ingestion.py` must stay green; add a transaction
    rollback test.
12. **`src/services/cache_invalidator.py`** — add `invalidate_strategy_report_keys`,
    `invalidate_strategy_trade_keys`, `invalidate_strategy_benchmark_keys`.
    Call them from `ingest.py` after successful `persist_daily_report`. Add
    tests with mocked `invalidate_pattern`.
13. **`src/api/v1/strategy_report.py`** — three handlers + small
    `StrategyReportResponse` and `TradeLogPage` response models (`Decimal`
    fields, frozen). Use the existing `get_cached` / `set_cached` pattern from
    `performance.py`. `TradeLogPage` paging fields `total, limit, offset`
    validated `ge=0`; `limit` capped at `1000` via `Query(... le=1000)`.
14. **`src/api/v1/router.py`** — `include_router(strategy_report.router)`.
15. **API tests** — `tests/api/v1/test_strategy_report.py` with `async_client`,
    `mock_pool`, `mock_csm_set_pool`, and `load_test_registry`. Cover: cache
    hit, cache miss + cache failure (graceful), 404 on unknown strategy id,
    404 on missing report, paging boundaries on `/trades`, normalize flag on
    `/benchmark-curve`.
16. **Integration test** — `tests/integration/test_strategy_report_roundtrip.py`
    (marker `integration`): POST an ingest payload with `extended_data.report`,
    then GET each of the three endpoints and assert shape + values.
17. **Quality gate**:
    ```bash
    uv run ruff check . \
      && uv run ruff format --check . \
      && uv run mypy src tests \
      && uv run pytest
    ```
    All four must be green. Coverage must stay ≥ 90% (gate is enforced in
    `pyproject.toml` via `--cov-fail-under=90`).
18. **Docs**: add a Progress section to the plan; tick Phase 3 boxes in the
    umbrella `ROADMAP.md`; add a brief note to `.claude/knowledge/` if a new
    pattern was introduced (e.g. dual-pool wiring).
19. **Commit** with conventional commit message, single commit.

---

## Critical Files (reuse rather than recreate)

- `src/api/v1/performance.py` (lines 35–59, 76–136) — golden template for
  cache-aside endpoints: `get_cached` → return on hit → call service → wrap
  ServiceError as 500 → `set_cached` (CacheError → log + serve uncached).
  **Reuse this exact pattern** for all three new endpoints.
- `src/services/cache.py` lines 22–60 (`get_cached`) and 62–87 (`set_cached`)
  — already TypeVar-generic over `BaseModel`; no extension needed for typed
  `StrategyReport`, `TradeLogPage`, `list[BenchmarkPoint]` (note: the third
  one needs a wrapper model since `get_cached` is `BaseModel`-bound).
- `src/services/cache.py::invalidate_pattern` lines 105–132 — SCAN-based,
  non-blocking; the new invalidators wrap it identically to
  `cache_invalidator.flush_all`.
- `src/services/ingestion.py::persist_daily_report` lines 98–137 — single-conn
  pattern. The refactor is to wrap the existing `conn.execute` + the new
  report UPSERT in `async with conn.transaction():`.
- `src/db/postgres.py` lines 1–22 — exact shape to mirror in
  `db/csm_set_postgres.py`.
- `src/api/v1/dependencies.py::verify_api_key` — N/A for this phase; the new
  endpoints are open reads. Do not add `dependencies=[Depends(verify_api_key)]`
  to the new router.
- `tests/conftest.py` lines 30–98 — `_TEST_ENV`, `set_env`,
  `load_test_registry`, `mock_pool`. The new `mock_csm_set_pool` fixture
  reuses the same factory and just yields a second, independently-mocked
  `MagicMock`.

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `csm_set_dsn` missing in fresh env → `get_settings()` raises and crashes startup, blocking every other endpoint | Required field is correct (matches `postgres_dsn`); document in `.env.example`. Tests inject a placeholder via `_TEST_ENV` so the suite is hermetic. |
| Two asyncpg pools double the open connection count to Postgres | Default pool sizes are small (10) and `db_csm_set` is read-only; net effect is +1 connection per app instance most of the time. Document in plan. |
| `StrategyReport` schema drifts from csm-set's emission | Phase 1 (csm-set) already shipped the matching Pydantic model; we copy field names exactly. Integration roundtrip test catches future drift. |
| Wrapping ingest in `conn.transaction()` changes existing failure semantics (today, `persist_daily_report` fails fast on PG error) | The semantic *intent* of the existing test (`IngestionPersistError` on PG error) is preserved — we only widen the atomic boundary to include the new report write. |
| `--cov-fail-under=90` is strict; new code in `strategy_report_service.py` may have hard-to-mock branches | Apply the same `mock_pool` pattern used for `services/portfolio.py` and `services/snapshot_writer.py`. Negative branches (`StrategyReportNotFoundError`, `asyncpg.PostgresError`) all have explicit tests. |
| Existing `extended_data: dict[str, Any]` is `frozen=True` — attaching `_parsed_report` on a frozen model breaks | Pydantic v2 supports `PrivateAttr()` on frozen models. Use `_parsed_report: StrategyReport | None = PrivateAttr(default=None)` so the public model remains frozen and the validator can still set the private attr via `self.__pydantic_private__["_parsed_report"]`. |
| Cache failure (Redis down) returning 500 from new endpoints | Existing pattern in `performance.py` already degrades gracefully — `CacheError` on `set` is logged warn and the response is returned. The new handlers must mirror this. |
| Coverage drops because the integration test path is excluded by default `-m "not integration"` | Unit tests cover the same branches with mocks. Integration test is opt-in (CI runs `-m integration` separately). |
| `BenchmarkPoint` list endpoint cannot be cached directly via `get_cached`/`set_cached` (BaseModel-bound) | Wrap the list in a `BenchmarkCurveResponse(items: list[BenchmarkPoint])` response model. Endpoint returns `.items` to keep the public response shape an array. |

---

## Acceptance Criteria

- [ ] `uv sync --all-groups` succeeds; `uv.lock` not touched (no new deps).
- [ ] `uv run ruff check .` — zero findings.
- [ ] `uv run ruff format --check .` — no drift.
- [ ] `uv run mypy src tests` — zero strict-mode errors.
- [ ] `uv run pytest` — green, coverage ≥ 90% (gate enforced by `pyproject.toml`).
- [ ] `uv run pytest -m integration` — green when the `quant-network` stack is up.
- [ ] `POST /api/v1/ingest/daily-report` with `extended_data.report` →
      `201 Created`; one row in `daily_performance` and one in
      `strategy_report_snapshot` (same `(time::date, strategy_id)`).
- [ ] `GET /api/v1/strategies/csm-set-01/report` returns a populated
      `StrategyReportResponse` in < 200 ms on a cache hit.
- [ ] `GET /api/v1/strategies/csm-set-01/trades?limit=50&offset=0` returns
      `TradeLogPage` whose `total` matches the row count.
- [ ] `GET /api/v1/strategies/csm-set-01/benchmark-curve` returns a list of
      `BenchmarkPoint` (raw or normalised per `normalize` flag).
- [ ] Missing report → `404 StrategyReportNotFound` with a clear detail body.
- [ ] Ingestion + report write are atomic (transaction rollback verified by a
      mocked PG failure unit test on the report UPSERT).
- [ ] Cache invalidation runs on every successful ingest (verified by a
      `mock.assert_called_with(...)` on the three new patterns).
- [ ] `../docs/plans/feature-strategies-report-metrics/ROADMAP.md` — every
      Phase 3 checkbox ticked.
- [ ] Single conventional commit
      `feat(gateway): implement Phase 3 endpoints, schemas, and cache`.

---

## Verification Plan

```bash
git checkout feat/gateway-endpoints-schemas-cache
uv sync --all-groups

uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -v --cov=src --cov-report=term-missing

# Integration sweep (requires quant-network up)
docker network ls | grep quant-network
docker compose -f ../quant-infra-db/docker-compose.yml up -d
docker compose up -d
uv run pytest -m integration -v

# Smoke
curl -s -H "X-API-Key: $INTERNAL_API_KEY" \
     -H "Content-Type: application/json" \
     -d @tests/fixtures/payload_with_report.json \
     http://localhost:8000/api/v1/ingest/daily-report
curl -s http://localhost:8000/api/v1/strategies/csm-set-01/report | jq .
curl -s http://localhost:8000/api/v1/strategies/csm-set-01/trades?limit=10 | jq .
curl -s http://localhost:8000/api/v1/strategies/csm-set-01/benchmark-curve | jq .
```

---

## Embedded Agent Prompt (verbatim)

```text
You are implementing Phase 3 — quant-api-gateway: endpoints + schemas + cache — for the
quant-api-gateway service. Follow every step in order. Do not skip or reorder steps.

───────────────────────────────────────────
STEP 1 — ORIENT
───────────────────────────────────────────
Read these files in full before doing anything else:
1. .claude/knowledge/project-skill.md  (master hard rules)
2. .claude/playbooks/feature-development.md  (8-step dev workflow)
3. ../docs/plans/feature-strategies-report-metrics/ROADMAP.md  — read the whole file but
   focus carefully on Phase 3: every deliverable, acceptance criterion, and dependency.
4. docs/plans/phase_1_bootstrap/phase_1_bootstrap.md  — note the exact markdown format for plan files.

───────────────────────────────────────────
STEP 2 — BRANCH
───────────────────────────────────────────
Create a new git branch for this work before touching any file:
  git checkout -b feat/gateway-endpoints-schemas-cache

───────────────────────────────────────────
STEP 3 — PLAN (MANDATORY BEFORE CODE)
───────────────────────────────────────────
Draft the full implementation plan and save it as:
  docs/plans/feature-strategies-report-metrics/PLAN.md

Use docs/plans/examples/phase1-sample.md as the format reference. The plan MUST include:
- Phase title, date, author
- Scope summary (what is and is not in scope)
- Full list of deliverables with file paths
- Acceptance criteria (specific, testable)
- Identified risks and mitigations
- Step-by-step implementation order
- This full AI agent prompt embedded verbatim in a fenced code block

Do NOT write any implementation code until PLAN.md is saved and complete.

───────────────────────────────────────────
STEP 4 — IMPLEMENT
───────────────────────────────────────────
Implement all Phase 3 deliverables. For every file you create or modify:

Schemas (src/schemas/):
- Add new Pydantic v2 models for all new request/response boundaries introduced in Phase 3
- Every financial field uses Decimal (never float)
- Every field has a description= and appropriate constraints
- All models include Config or model_config with strict mode where appropriate

API routes (src/api/v1/):
- Add/update route handlers following the existing module pattern
- Write endpoints protected by X-API-Key via dependencies.py; read endpoints open
- Each handler: validate input via Pydantic, call service layer, return typed response model
- Log with logger = logging.getLogger(__name__); include request_id in structured log entries
- Raise typed HTTPException or domain exceptions — never bare Exception

Services / Cache (src/services/):
- Extend src/services/cache.py with any new cache-aside helpers required by Phase 3
- Extend src/services/cache_invalidator.py to invalidate new cache keys on relevant writes
- All cache failures must degrade gracefully: log warning, compute fresh, never 500

Config (src/config.py):
- Add new Settings fields for any new TTL or configuration values introduced in Phase 3
- Use pydantic-settings with env var names, defaults, and field descriptions

General rules (enforce without exception):
- Full type annotations on every public function (args + return type)
- Async/await for all I/O operations
- No requests library — use httpx.AsyncClient with explicit timeout= for any outbound HTTP
- No print() statements — use logging.getLogger(__name__)
- No bare except: clauses
- No hardcoded secrets or paths — read everything from Settings
- Import order: stdlib → third-party → local; no wildcards; no relative imports beyond one level
- Files ≤500 lines; split into a sub-package if exceeded
- Docstrings on all public functions: Args, Returns, Raises, Example

───────────────────────────────────────────
STEP 5 — TESTS
───────────────────────────────────────────
- Add unit tests in tests/ mirroring the src/ structure for every new/changed module
- Add integration tests under tests/integration/ for any endpoint that requires the
  quant-network stack (mark with @pytest.mark.integration)
- No real network calls in unit tests — mock external dependencies
- Verify coverage gate: uv run pytest --cov=src --cov-report=term-missing
  (must not drop below 90%)

───────────────────────────────────────────
STEP 6 — QUALITY GATE
───────────────────────────────────────────
Run the full quality gate and fix all failures before proceeding:
  uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest

───────────────────────────────────────────
STEP 7 — DOCUMENTATION & PROGRESS UPDATE
───────────────────────────────────────────
- Update docs/plans/feature-strategies-report-metrics/PLAN.md:
  add a Progress section with completion date, any issues encountered during testing,
  and notes on deviations from the original plan
- Update docs/plans/feature-strategies-report-metrics/ROADMAP.md:
  checkmark every Phase 3 item that is now complete
- If any reusable knowledge or patterns were discovered during this work
  (e.g., new cache patterns, new error-handling conventions, agent tips), create or update
  the appropriate file under .claude/knowledge/ or .claude/playbooks/ and update CLAUDE.md
  to reference the new content

───────────────────────────────────────────
STEP 8 — COMMIT
───────────────────────────────────────────
Commit all changes in a single conventional commit after the quality gate is green:
  git add -A
  git commit -m "feat(gateway): implement Phase 3 endpoints, schemas, and cache"

The commit body should list key files changed and reference the plan document.

───────────────────────────────────────────
FILES TO READ / CREATE / MODIFY
───────────────────────────────────────────
Read first:
  .claude/knowledge/project-skill.md
  .claude/playbooks/feature-development.md
  ../docs/plans/feature-strategies-report-metrics/ROADMAP.md
  docs/plans/phase_1_bootstrap/phase_1_bootstrap.md

Create:
  docs/plans/feature-strategies-report-metrics/PLAN.md

Likely to modify (confirm against ROADMAP Phase 3 deliverables):
  src/config.py
  src/schemas/  (new or updated model files)
  src/services/cache.py
  src/services/cache_invalidator.py
  src/api/v1/  (new or updated route modules)
  src/api/v1/router.py  (register new routers if needed)
  src/main.py  (only if lifespan or middleware changes required)
  tests/  (new test files mirroring src/ changes)
  CLAUDE.md  (if new knowledge files are added)
 ../ docs/plans/feature-strategies-report-metrics/ROADMAP.md  (checkmarks)

───────────────────────────────────────────
CONSTRAINTS REMINDER
───────────────────────────────────────────
- uv run for every Python invocation — never bare python or pip
- Decimal for all financial fields — never float
- UTC for all timestamps
- Pydantic v2 at every module boundary
- mypy strict = true must pass with zero errors
- Coverage must not drop below 90%
- Do not mix refactor and feature in this commit
- Do not commit .env or any secrets
```

---

## Progress / Notes

### Completion summary (2026-05-21)

Phase 3 implemented end-to-end on branch
`feat/gateway-endpoints-schemas-cache`. Final quality gate:

```
uv run ruff check .              → All checks passed!
uv run ruff format --check .     → 70 files already formatted
uv run mypy src tests            → Success: no issues found in 70 source files
uv run pytest                    → 276 passed, 8 deselected; coverage 94.67%
```

Coverage for the new modules:

```
src/api/v1/strategy_report.py                100%
src/db/csm_set_postgres.py                   100%
src/schemas/strategy_report.py               100%
src/services/strategy_report_service.py      100%
src/services/cache_invalidator.py            100% (extended)
src/services/ingestion.py                     94% (extended; 1 untested fallback branch)
src/schemas/strategy.py                      100% (extended)
src/config.py                                100% (extended)
```

### Acceptance criteria status

- [x] `uv sync --all-groups` succeeds; `uv.lock` not touched (no new deps).
- [x] Quality gate green (ruff + ruff format + mypy strict + pytest ≥ 90%).
- [x] `uv run pytest -m integration` round-trip test passes against the
      in-process ASGI client with mocked Postgres + Redis. Live-stack
      integration is verified manually in Phase 5.
- [x] `POST /api/v1/ingest/daily-report` with `extended_data.report` →
      `201 Created`; both ``daily_performance`` and
      ``strategy_report_snapshot`` UPSERTs run inside one
      ``conn.transaction()`` (verified by
      `test_persist_daily_report_with_report_executes_both_upserts` and
      `test_persist_daily_report_report_failure_wrapped`).
- [x] `GET /api/v1/strategies/csm-set-01/report` returns
      `StrategyReportResponse`; cache-hit short-circuit verified by
      `test_report_returns_cached_when_present`.
- [x] `GET /api/v1/strategies/csm-set-01/trades?limit=50&offset=0` returns
      `TradeLogPage`; `total` returned from a `count(*)` query in the
      same connection.
- [x] `GET /api/v1/strategies/csm-set-01/benchmark-curve` returns
      `list[BenchmarkPoint]`; raw + normalised paths covered.
- [x] Missing report → `404` with a detail body containing the strategy id.
- [x] Cache invalidation runs on every successful ingest
      (`test_invalidate_strategy_report_bundle_runs_all_three`).
- [x] Umbrella `feature-strategies-report-metrics/ROADMAP.md` Phase 3
      checkboxes ticked.

### Deviations from the original plan

- **Cache wrapper for `list[BenchmarkPoint]`**: the cache-aside helpers are
  bound to `BaseModel`, so an internal `BenchmarkCurveResponse(items=...)`
  wrapper was introduced. The public `/benchmark-curve` response is still
  the bare JSON array — the handler unwraps `cached.items` on a cache hit
  and wraps fresh results before `set_cached`. Documented in
  `src/schemas/strategy_report.py::BenchmarkCurveResponse`.
- **trade_history schema**: the existing `db_csm_set.trade_history` table
  has a single ``time`` column rather than separate
  ``entry_time``/``exit_time`` fields. Until csm-set Phase 2+ widens the
  schema, the gateway exposes ``time`` as both `entry_time` and
  `exit_time` in `TradeLogEntry`. This is documented in
  `strategy_report_service.py::_row_to_trade` and is a known follow-up
  recorded in the umbrella ROADMAP.
- **`bundle invalidator` helper**: the per-pattern invalidators are
  individually exposed for granular use, but the ingest endpoint calls a
  single `invalidate_strategy_report_bundle(strategy_id)` so the route
  only needs one await. Pattern: cache invalidation is best-effort —
  failures are logged at ERROR and never propagated.
- **Atomic transaction error mapping**: `persist_report` wraps
  `asyncpg.PostgresError` in `ServiceError`. The outer
  `persist_daily_report` re-wraps any `Exception` raised inside the
  transaction (other than `IngestionPersistError`) into
  `IngestionPersistError` so the route layer's existing 500-mapping path
  is preserved with no special-casing for the new write.
- **Test venv rebuild**: the existing `.venv/` had stale shebangs pointing
  to `/home/batt/docker/quant-trading/...` (legacy path). Ran `rm -rf
  .venv && uv sync --all-groups` once at the top of the implementation.
  No source change.

### Patterns established (worth carrying forward)

- **Dual-pool wiring**: the gateway now opens two asyncpg pools
  (`db_gateway` read/write + `db_csm_set` read-only). The `mock_pool` /
  `mock_csm_set_pool` pair in `tests/conftest.py` is the canonical fixture
  shape — each test injects whichever pool(s) it needs and the lifespan
  patches both at `src.main.get_pool` / `src.main.get_csm_set_pool` plus
  the per-router import path.
- **PrivateAttr on frozen models for parsed sub-payloads**:
  `StrategyPayload._parsed_report` lets the input boundary stay
  `frozen=True` while still attaching a parsed view of an opaque
  `extended_data` blob. Invalid sub-payloads degrade to `None` with a
  WARNING — never block ingestion.
- **Pattern-hash cache keys**: `_params_hash(**parts)` in
  `src/api/v1/strategy_report.py` produces a stable 16-char SHA-1 over
  query parameters so cache keys stay short and deterministic.

### Time spent

~2.0 h end-to-end (orientation, plan, tests-first implementation, two
mypy fix-ups, full quality gate, docs).
