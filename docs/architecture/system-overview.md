# System Overview

**Available since:** v0.1.0

High-level design of the quant-api-gateway — how the modules connect, the data-flow rules, and the runtime topology.

---

## Module Map

```
src/
├── main.py                  FastAPI app, lifespan, /health, RequestIDMiddleware
├── config.py                Pydantic Settings (env vars → typed config)
├── logging_config.py        JSON structured logging, request_id ContextVar
│
├── api/v1/                  ← HTTP boundary (endpoints)
│   ├── router.py            Mounts all sub-routers under /api/v1
│   ├── dependencies.py      verify_api_key (X-API-Key header)
│   ├── ingest.py            POST /api/v1/ingest/daily-report
│   ├── performance.py       GET /overall-performance, /strategies/{id}/performance
│   ├── strategies.py        GET /strategies, /{id}, /{id}/equity-curve
│   ├── portfolio.py         GET /portfolio/snapshot, /{date}, /equity-curve
│   └── admin.py             POST /admin/cache/flush
│
├── schemas/                 ← Pydantic v2 models (boundary contracts)
│   ├── strategy.py          StrategyPayload (input), EquityPoint, etc.
│   ├── gateway.py           OverallPerformanceResponse, etc. (output)
│   ├── registry.py          StrategyConfig, StrategyRegistry
│   └── errors.py            SchemaValidationError
│
├── services/                ← Business logic (pure + I/O)
│   ├── aggregator.py        calculate_weighted_return, merge_equity_curves,
│   │                        calculate_combined_drawdown (pure — no I/O)
│   ├── cache.py             get_cached, set_cached, invalidate_key,
│   │                        invalidate_pattern (Redis I/O)
│   ├── cache_invalidator.py invalidate_overall_cache, flush_all
│   ├── ingestion.py         persist_daily_report, _payload_to_row
│   ├── snapshot_writer.py   maybe_write_snapshot, _compute_aggregates
│   ├── strategy_registry.py load_registry, get_registry
│   ├── performance.py       compute_overall_performance,
│   │                        compute_strategy_performance,
│   │                        compute_strategy_performance_range
│   ├── portfolio.py         query_latest_snapshot,
│   │                        query_snapshot_by_date,
│   │                        compute_portfolio_equity_curve
│   └── errors.py            ServiceError, CacheError, etc.
│
└── db/                      ← Infrastructure singletons
    ├── postgres.py          asyncpg.Pool (get_pool / close_pool)
    ├── mongo.py             motor AsyncIOMotorClient (get_client / close_client)
    └── redis_client.py      redis.asyncio.Redis (get_redis / close_redis)
```

---

## Data Flow

One-way, layered. Lower layers must not import from higher ones:

```
External I/O → src/db → src/services → src/schemas → src/api → src/main.py
```

### Ingestion path

```
quant-csm-set (or any strategy service)
    │ POST /api/v1/ingest/daily-report
    │ Header: X-API-Key: <secret>
    │ Body: StrategyPayload (JSON)
    ▼
api/v1/ingest.py          ← verify_api_key, Pydantic validation
    │
    ▼
services/ingestion.py     ← persist_daily_report() → INSERT INTO daily_performance
    │
    ▼
services/snapshot_writer  ← maybe_write_snapshot()
    │                         Check: have ALL active strategies reported today?
    │                         If yes: compute aggregates → upsert portfolio_snapshot
    │                         Then: invalidate cache keys (best-effort)
    ▼
db/postgres.py            ← asyncpg.Pool.execute / fetch
```

### Read path (cache-aside)

```
Dashboard / Client
    │ GET /api/v1/overall-performance
    ▼
api/v1/performance.py     ← cache-aside orchestration
    │
    ├── services/cache.py  ← get_cached("overall_performance")
    │   ├── hit → return cached OverallPerformanceResponse
    │   └── miss ↓
    │
    ├── db/postgres.py     ← query latest rows per strategy
    ├── services/performance.py ← compute OverallPerformanceResponse
    ├── services/cache.py  ← set_cached("overall_performance", result, ttl=300)
    │                         (best-effort — failure logged, response still returned)
    └── return response
```

---

## Runtime Topology

```
┌────────────────── quant-network ──────────────────┐
│                                                    │
│  ┌──────────────────┐  ┌────────────────────────┐ │
│  │ quant-api-gateway│  │ quant-redis            │ │
│  │ (FastAPI:8000)   │  │ (redis:7-alpine:6379)  │ │
│  └────────┬─────────┘  └────────────────────────┘ │
│           │                                         │
│  ┌────────┴─────────┐  ┌────────────────────────┐ │
│  │ quant-postgres   │  │ quant-mongo            │ │
│  │ (PostgreSQL:5432)│  │ (MongoDB:27017)        │ │
│  │ db_gateway       │  │                        │ │
│  └──────────────────┘  └────────────────────────┘ │
│                                                    │
│  ┌──────────────────┐                             │
│  │ csm-set-csm-1    │ (csm:8000)                  │
│  │ (Strategy Svc)   │                             │
│  └──────────────────┘                             │
└────────────────────────────────────────────────────┘
```

All containers are on `quant-network` (external Docker network, created once by `quant-infra-db`). Hostnames resolve by Docker compose service name:
- `quant-api-gateway:8000`
- `quant-redis:6379`
- `quant-postgres:5432`
- `quant-mongo:27017`
- `csm:8000`

---

## Application Lifecycle

### Startup (in order)

1. `get_settings()` — read and validate all env vars
2. `configure_logging(settings)` — install JSON formatter as root handler
3. `strategy_registry.load_registry(path)` — load `strategies.json`, fail-fast on error
4. `get_pool()` — open asyncpg connection pool to `quant-postgres`
5. `get_redis()` — open Redis connection to `quant-redis`

### Shutdown

1. `close_pool()` — close asyncpg pool
2. `close_redis()` — close Redis connection
3. `strategy_registry.clear_registry()` — clear in-memory state

### Per-request

1. `RequestIDMiddleware.dispatch()`:
   - Generate `uuid4`
   - Set `request_id_var` ContextVar (so log formatter includes it)
   - Store on `request.state.request_id`
   - Attach `X-Request-ID` response header

---

## Hard Rules

1. **`uv run` everywhere** — never bare `python`/`pip`
2. **Async-first I/O** — `httpx.AsyncClient`, `asyncpg`, `redis.asyncio`; `requests` forbidden
3. **Pydantic at boundaries** — data crossing module boundaries must be a Pydantic model, never a raw dict
4. **Type hints everywhere** — full annotations on all public functions, no bare `Any`
5. **≥90% coverage** — enforced by `--cov-fail-under=90`
6. **Logging, not `print`** — structured JSON via `logging.getLogger(__name__)`
7. **Timestamps in UTC** — internal enforcement; localize only at presentation boundaries
8. **Conventional Commits** — `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`
9. **No secrets in repo** — all config via env vars; `.env` is gitignored
10. **File size ≤500 lines** — split into packages when exceeded

---

## See Also

- [Data Flow Diagram](data-flow.md) — detailed step-by-step with SQL and cache keys
- [Module Boundaries](module-boundaries.md) — import rules and layer diagram
- [Quality Gate Reference](../operations/quality-gate.md) — exact commands and thresholds
- [PROJECT.md](../PROJECT.md) — complete module-by-module reference
