# Phase 7 — Operations & Quality Gate

| Field | Value |
|---|---|
| Phase | 7 — Operations & Quality Gate |
| Date | 2026-05-15 |
| Author | Claude (Opus 4.7), acting on lumduan's behalf |
| Branch | `feat/phase-7-operations-quality-gate` |
| Base branch | `main` |
| Target | `main` |
| Linked roadmap | `docs/plans/ROADMAP.md` §7.1–§7.4 |

---

## Context

Phase 6 delivered 7 read endpoints with cache-aside across `performance.py`, `portfolio.py`, and `strategies.py`. Coverage stands at 95.64% (199 tests passing). Two items were explicitly deferred to Phase 7:

- **DD#3**: `GET /api/v1/strategies/{strategy_id}/performance` only returns the latest snapshot (cached). Date-range query params `?from_date=&to_date=` need to be implemented.
- **DD#4**: `GET /api/v1/portfolio/equity-curve` accepts `?normalize=false` in the query but does not honor it — `merge_equity_curves` in `aggregator.py` always normalizes to base 100.

The codebase uses raw `logging` with `%`-formatting. No JSON structured logging, no request-ID middleware. The Dockerfile is a basic multi-stage build without a non-root user, HEALTHCHECK instruction, or `.dockerignore`. The README still shows only Phase 1 endpoints.

Phase 7 transforms the service from "feature-complete" to "production-ready" by hardening operations, adding integration tests, closing the Phase 6 deferred items, and raising the quality bar to ≥90% coverage.

## Objective

Harden the gateway for production: structured JSON logging with request-ID tracing, Docker security hardening (non-root user, HEALTHCHECK), date-range querying on strategy performance, `normalize=false` support on portfolio equity curve, integration test suite against real infrastructure, README endpoint reference table, and coverage raised to ≥90%.

## Scope

### In scope

1. **Structured JSON logging** — `src/logging_config.py` with custom `JSONFormatter`, `configure_logging(settings)`, wired into main.py lifespan before anything else.
2. **Request-ID middleware** — `uuid4` per request, `X-Request-ID` response header, `request_id` in log context via `contextvars`.
3. **Docker hardening** — multi-stage build, non-root `appuser` (uid 1000), `HEALTHCHECK`, `EXPOSE 8000`, `PYTHONDONTWRITEBYTECODE=1`, `.dockerignore`.
4. **docker-compose healthcheck tuning** — Redis `interval: 10s`, `timeout: 5s`, `retries: 5`. Gateway `start_period: 40s`.
5. **Date-range querying** — `?from_date=&to_date=` on `GET /api/v1/strategies/{strategy_id}/performance`.
6. **`normalize=false` honored** — `merge_equity_curves` accepts `normalize: bool`, `compute_portfolio_equity_curve` forwards it, API forwards it.
7. **Integration tests** — `tests/integration/` package with `conftest.py` and `test_end_to_end.py`, gated behind `-m integration`.
8. **README endpoint reference table** — all 11 endpoints with Method, Path, Auth, Description, Cache TTL columns.
9. **Coverage gate raised** — `--cov-fail-under=90`.

### Out of scope (Phase 8+)

- CI/CD pipeline (GitHub Actions workflow changes)
- Prometheus metrics / OpenTelemetry
- Alerting rules
- Rate limiting
- API versioning (v2)

---

## Design Decisions

### 1. Custom `logging.Formatter` subclass instead of `python-json-logger`

**Chosen:** A lightweight `JSONFormatter(logging.Formatter)` in `src/logging_config.py` that emits one-line JSON records with `timestamp`, `level`, `logger`, `message`, plus any `extra` kwargs.

**Why:** The project rule is "prefer stdlib over third-party dependencies." A custom formatter is ~30 lines, avoids a new dependency, and gives full control over the JSON shape. The `python-json-logger` package would add a dependency for a feature that's trivial to implement.

### 2. `contextvars` for request-ID propagation

**Chosen:** A `ContextVar[str | None]` set by a FastAPI middleware, read by the `JSONFormatter` via `logging.LogRecord` inspection.

**Why:** `contextvars` is the stdlib mechanism for request-scoped state in async code. It works correctly with `asyncio` task scheduling (unlike `threading.local`). The middleware sets it; the formatter reads it — no global state, no races.

### 3. Date-range query returns `list[StrategyPerformanceResponse]`, not a new schema

**Chosen:** When `?from_date=&to_date=` are both present, the response is `list[StrategyPerformanceResponse]`. When absent, the response stays `StrategyPerformanceResponse` (latest snapshot, cached).

**Why:** A single `StrategyPerformanceResponse` already carries all fields needed per-day. A new "range response" schema would add unnecessary indirection.

### 4. `normalize: bool` added to `merge_equity_curves` signature with backward-compatible default

**Chosen:** Add `normalize: bool = True` to `merge_equity_curves(curves, weights, normalize=True)`. When `False`, skip the normalization step (division by first value × 100) and forward-fill raw cumulative values.

**Why:** The function already computes raw series before normalization. Skipping the normalization step is a one-line conditional. Defaulting to `True` preserves all existing callers (snapshot writer, combined drawdown) whose behavior must not change.

### 5. Integration tests excluded from default `uv run pytest` run

**Chosen:** Add `-m "not integration"` to `addopts` in `pyproject.toml`. Register `integration` marker. Integration tests run via `uv run pytest -m integration -v`.

**Why:** Integration tests require a running `quant-network` stack (Postgres, Redis). CI and local dev should not require infrastructure for fast feedback.

### 6. Non-root user in Dockerfile

**Chosen:** Create `appuser` (uid 1000) in the runtime stage, `chown` the venv and app directory, `USER appuser`.

**Why:** Container security best practice. Running as root means a compromised process has root on the host. A non-root user limits blast radius.

### 7. `PYTHONDONTWRITEBYTECODE=1` in Dockerfile

**Chosen:** Add to runtime stage ENV alongside `PYTHONUNBUFFERED=1`.

**Why:** No sense writing `.pyc` files in a container — they bloat the image and are never reused.

---

## Schema / Module Design

### `src/logging_config.py` (new)

```
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

class JSONFormatter(logging.Formatter):
    def format(self, record) -> str:
        # Emit: timestamp (UTC ISO-8601), level, logger, message
        # Include request_id from ContextVar if set
        # Include any extra kwargs from the log record
        # Include exc_info if present
```

### Date-range querying (`src/api/v1/performance.py`)

- Add `from_date: date | None` and `to_date: date | None` query params
- If both present: call `compute_strategy_performance_range`, return `list[StrategyPerformanceResponse]` (no caching)
- If neither present: existing latest-snapshot cached behavior
- If only one present: `422` with "Both from_date and to_date are required for range queries"
- Response model becomes `StrategyPerformanceResponse | list[StrategyPerformanceResponse]`

### `normalize` passthrough chain

- `merge_equity_curves(curves, weights, normalize=True)` in `aggregator.py`
- `compute_portfolio_equity_curve(pool, registry, normalize=True)` in `services/portfolio.py`
- `get_portfolio_equity_curve(normalize=True)` in `api/v1/portfolio.py` forwards to service

---

## Deliverables

### Created

| File | Description |
|---|---|
| `src/logging_config.py` | JSON formatter + `configure_logging()` + `request_id_var` |
| `tests/integration/__init__.py` | Package marker |
| `tests/integration/conftest.py` | `real_pool`, `real_redis`, `integration_client` fixtures |
| `tests/integration/test_end_to_end.py` | 6 integration tests |
| `.dockerignore` | Exclude git, caches, tests, docs, etc. |
| `docs/plans/phase_7_operations_quality_gate/phase_7_operations_quality_gate.md` | This plan |

### Modified

| File | Change |
|---|---|
| `src/main.py` | Add `configure_logging(settings)` before other startup; add `RequestIDMiddleware` |
| `src/api/v1/performance.py` | Add `from_date`/`to_date` query params to `get_strategy_performance`; range response type |
| `src/api/v1/portfolio.py` | Forward `normalize` param to `compute_portfolio_equity_curve` |
| `src/services/performance.py` | Add `compute_strategy_performance_range` function |
| `src/services/portfolio.py` | Add `normalize` param to `compute_portfolio_equity_curve`; forward to aggregator |
| `src/services/aggregator.py` | Add `normalize: bool = True` param to `merge_equity_curves` |
| `Dockerfile` | Rewrite with non-root user, HEALTHCHECK, PYTHONDONTWRITEBYTECODE |
| `docker-compose.yml` | Tune healthcheck intervals (Redis: 10s/5s/5; Gateway: start_period 40s) |
| `pyproject.toml` | Register `integration` marker; add `-m "not integration"` to addopts; raise `--cov-fail-under=90` |
| `README.md` | Replace Phase 1 endpoint table with full API Endpoints reference table; update status |
| `docs/plans/ROADMAP.md` | Tick §7 acceptance criteria; advance status to Phase 8 |

### Untouched

- `src/schemas/` — no schema changes needed
- `src/services/cache.py`, `src/services/cache_invalidator.py` — stable
- `src/services/ingestion.py`, `src/services/snapshot_writer.py` — stable
- `src/services/strategy_registry.py` — stable
- `src/db/{postgres,redis_client,mongo}.py` — stable
- `src/config.py` — no new settings needed
- `src/api/v1/ingest.py`, `src/api/v1/admin.py`, `src/api/v1/strategies.py` — stable
- `src/api/v1/router.py`, `src/api/v1/dependencies.py` — stable
- All existing tests — no changes needed

---

## Acceptance Criteria

### Overall Exit Criteria

1. `docker compose up -d` from a fresh clone → `docker compose ps` shows `quant-api-gateway` and `quant-redis` as `(healthy)`
2. `GET /health` returns `{"status": "ok"}` from `localhost:8000` AND from inside `quant-network` via hostname `quant-api-gateway`
3. `POST /api/v1/ingest/daily-report` accepts a `StrategyPayload` from `quant-csm-set` (valid `X-API-Key`) and writes a row to `db_gateway.daily_performance`
4. `GET /api/v1/overall-performance` returns a complete `OverallPerformanceResponse` in < 200 ms on a cache hit
5. Cache invalidation end-to-end: a new ingestion clears `overall_performance` key; the next request returns fresh data
6. Quality gate green: `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest --cov=src --cov-fail-under=90`

### Structured logging
- [ ] `configure_logging(settings)` emits JSON-formatted log lines
- [ ] Every log line includes `timestamp` (UTC ISO-8601), `level`, `logger`, `message`
- [ ] Request-ID middleware adds `X-Request-ID` response header and `request_id` to log context
- [ ] No request body, auth headers, or secrets in log output

### Docker hardening
- [ ] Multi-stage Dockerfile builds successfully
- [ ] Container runs as non-root `appuser` (uid 1000)
- [ ] `HEALTHCHECK` instruction present and functional
- [ ] `.dockerignore` excludes `.git`, `__pycache__`, tests, docs, etc.

### Date-range querying
- [ ] `GET /api/v1/strategies/{id}/performance?from_date=2026-01-01&to_date=2026-01-31` returns `list[StrategyPerformanceResponse]`
- [ ] No `?from_date`/`?to_date` → existing behavior (latest snapshot, cached)
- [ ] Only one of `from_date`/`to_date` → 422 with actionable message
- [ ] Empty range → empty list (not 404)

### normalize=false
- [ ] `GET /api/v1/portfolio/equity-curve?normalize=false` returns raw cumulative values (not base-100 normalized)
- [ ] `GET /api/v1/portfolio/equity-curve?normalize=true` (default) normalizes to base 100
- [ ] All existing callers (`calculate_combined_drawdown`, snapshot writer) unaffected

### Integration tests
- [ ] `tests/integration/test_end_to_end.py` contains 6 tests
- [ ] `uv run pytest -m integration -v` runs against real infrastructure
- [ ] `uv run pytest` (default) excludes integration tests

### README
- [ ] `## API Endpoints` table with all 11 endpoints and columns: Method, Path, Auth, Description, Cache TTL

### Quality gate
- [ ] `uv run ruff check .` — zero findings
- [ ] `uv run ruff format --check .` — no drift
- [ ] `uv run mypy src tests` — zero strict-mode errors
- [ ] `uv run pytest -v --cov=src --cov-report=term-missing --cov-fail-under=90` — green

---

## Test Strategy

### `tests/integration/test_end_to_end.py` (new)

| Test | Verifies |
|---|---|
| `test_health_endpoint_returns_ok` | `GET /health` → 200 `{"status": "ok"}` |
| `test_ingest_daily_report_writes_to_postgres` | POST with valid `X-API-Key` → 201; row in DB |
| `test_overall_performance_cache_hit_under_200ms` | Two requests; second < 200ms |
| `test_cache_invalidation_on_ingest` | Ingest → flush Redis → GET returns fresh data |
| `test_get_strategy_performance_range` | `?from_date=&to_date=` returns list |
| `test_portfolio_equity_curve_no_normalize` | `?normalize=false` returns raw values |

### Integration test conftest fixtures

- `real_pool`: Connects to Postgres using `DATABASE_URL` from env (skip if not set)
- `real_redis`: Connects to Redis using `REDIS_URL` from env (skip if not set)
- `integration_client`: `httpx.AsyncClient` against real app at `http://localhost:8000` (base URL from env, default `http://localhost:8000`)

### Mocking approach

- Integration tests use real infrastructure (no mocking)
- Unit tests are unchanged — they continue to mock `get_pool`, `get_redis`, and registry
- The `integration` marker in `pyproject.toml` gates execution

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `StrategyPerformanceResponse \| list[StrategyPerformanceResponse]` may confuse FastAPI OpenAPI schema generation | Test `/openapi.json` after change. If OpenAPI doesn't represent the union cleanly, split into two endpoints instead. |
| `mypy` strict mode may reject `ContextVar[str \| None]` assignment | Use explicit `ContextVar[str \| None]("request_id", default=None)` typing. |
| `BaseHTTPMiddleware` can have subtle issues with `StreamingResponse` | We don't stream responses — all endpoints return JSON. Safe to use. |
| Non-root user may lack permissions to bind port 8000 | Port 8000 is >1024, so non-root can bind. Verified. |
| Integration tests require `quant-network` infrastructure running | Mark `integration` tests, exclude from default run. |
| `normalize=False` on raw equity values may produce very large or very small numbers | The `EquityPoint.value` field has `max_digits=18, decimal_places=4` — raw cumulative values should fit within this range. |
| `--cov-fail-under=90` may fail on existing 95.64% but edge cases in new code may dip | Run coverage first, identify gaps, add targeted tests if needed. |

---

## Implementation Order

1. Create branch — `git checkout -b feat/phase-7-operations-quality-gate`
2. Write and commit this plan file
3. Implement `src/logging_config.py` — JSON formatter + `configure_logging()`
4. Wire logging into `src/main.py` — call `configure_logging(settings)` in lifespan; add `RequestIDMiddleware`
5. Docker hardening — rewrite `Dockerfile`, create `.dockerignore`, tune `docker-compose.yml`
6. Implement date-range querying — `compute_strategy_performance_range` in `src/services/performance.py`; extend API endpoint in `src/api/v1/performance.py`
7. Honor `normalize=false` — add param to `merge_equity_curves` in `aggregator.py`, `compute_portfolio_equity_curve` in `services/portfolio.py`, forward from `api/v1/portfolio.py`
8. Create `tests/integration/` package with `conftest.py` and `test_end_to_end.py`
9. Update `pyproject.toml` — integration marker, addopts, cov-fail-under=90
10. Update `README.md` — API endpoint reference table; update status
11. Run full quality gate and fix all findings
12. Docker verification — build, compose up, health checks
13. Update `docs/plans/ROADMAP.md` — tick §7, advance to Phase 8
14. Fill in Progress / Notes in plan file
15. Commit + push + PR

---

## Verification Plan

```bash
# Branch check
git branch --show-current   # → feat/phase-7-operations-quality-gate

# Quality gate (must be green)
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -v --cov=src --cov-report=term-missing --cov-fail-under=90

# Integration tests (requires running stack)
uv run pytest -m integration -v

# Docker verification
docker build -t quant-api-gateway:phase7 .
docker compose up -d
docker compose ps   # both services (healthy)
curl -s http://localhost:8000/health   # → {"status":"ok"}
docker run --rm --network quant-network curlimages/curl \
  curl -s http://quant-api-gateway:8000/health   # → {"status":"ok"}
```

---

## Critical Files (reuse rather than recreate)

- `src/config.py` — `get_settings()` with `log_level` field (already present)
- `src/services/aggregator.py` — `merge_equity_curves` (add `normalize` param, don't rewrite)
- `src/services/performance.py` — `compute_strategy_performance` (add range variant, reuse `_row_to_strategy_performance`)
- `src/services/portfolio.py` — `compute_portfolio_equity_curve` (add `normalize` passthrough)
- `src/db/postgres.py` — `get_pool()` for date-range queries
- `src/api/v1/performance.py` — existing endpoint to extend with query params
- `src/api/v1/portfolio.py` — existing `normalize` param (just needs forwarding)
- `tests/conftest.py` — `set_env`, `async_client`, `mock_pool` fixtures reused as-is
- `pyproject.toml` — existing tool config to extend

---

## Agent Prompt (verbatim)

> You are implementing Phase 7 — Operations & Quality Gate for the quant-api-gateway project.
> Follow every step below precisely and in order. Do NOT skip steps or reorder them.
>
> ---
> ## Step 1 — Orientation
> [... full prompt from user message ...]

---

## Progress / Notes

*(Fill in after implementation)*

### Implementation date

### Quality-gate output

### Per-module coverage

### Dependency changes

### Deviations from the plan

### Problems encountered

### Time spent
