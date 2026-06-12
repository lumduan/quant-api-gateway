# quant-api-gateway — Project Documentation

> AI/LLM-friendly reference. Read this file to understand the entire codebase
> without reading the source. Generated from the complete Phase 1–7
> implementation history.

---

## 1. Project Identity

- **Name:** `quant-api-gateway`
- **Role:** Central Aggregator Service of the Quant Trading System
- **Runtime:** FastAPI container on Docker network `quant-network`
- **Python:** ≥3.11 (Docker base: `python:3.11-slim`)
- **Package manager:** `uv` (never `pip`, `poetry`, or `conda`)

### What it does

1. Accepts Daily Performance reports from Strategy Services (currently `quant-csm-set`) via `POST /api/v1/ingest/daily-report`
2. Validates payloads with Pydantic v2 at the boundary
3. Persists rows to PostgreSQL (`db_gateway.daily_performance`)
4. Computes capital-weighted return, combined drawdown, and merged equity curves via a pure aggregation engine
5. Caches aggregated results in Redis with configurable TTLs
6. Exposes 11 REST endpoints for the React Dashboard and other clients
7. Emits structured JSON logs with request-ID tracing

### Dependencies

| Package | Purpose |
|---|---|
| `fastapi>=0.111` | Web framework |
| `uvicorn[standard]>=0.29` | ASGI server |
| `asyncpg>=0.29` | PostgreSQL async driver |
| `motor>=3.4` | MongoDB async driver (future use) |
| `redis[asyncio]>=5.0` | Redis async client |
| `pydantic>=2.7` | Schema validation |
| `pydantic-settings>=2.2` | Env-var configuration |
| `httpx>=0.27` | Async HTTP client |
| `pandas>=2.2` | Equity-curve date alignment |

---

## 2. Architecture

### Data-flow layers (one-way, lower must not import from higher)

```
External I/O → src/data (db/) → src/services/ → src/schemas/ → src/api/ → src/main.py
```

### File structure

```
quant-api-gateway/
├── src/
│   ├── main.py                   # FastAPI app, lifespan, /health, RequestIDMiddleware
│   ├── config.py                 # Pydantic Settings (env vars)
│   ├── logging_config.py         # JSON structured logging, request_id ContextVar
│   │
│   ├── api/v1/
│   │   ├── router.py             # Mounts all sub-routers under /api/v1
│   │   ├── dependencies.py       # verify_api_key dependency (X-API-Key header)
│   │   ├── ingest.py             # POST /api/v1/ingest/daily-report
│   │   ├── performance.py        # GET /api/v1/overall-performance, /strategies/{id}/performance
│   │   ├── strategies.py         # GET /api/v1/strategies, /strategies/{id}, /{id}/equity-curve
│   │   ├── portfolio.py          # GET /api/v1/portfolio/snapshot, /{date}, /equity-curve
│   │   └── admin.py              # POST /api/v1/admin/cache/flush
│   │
│   ├── schemas/
│   │   ├── strategy.py           # StrategyPayload (input), EquityPoint, PerformanceMetrics, etc.
│   │   ├── gateway.py            # Output response models (OverallPerformanceResponse, etc.)
│   │   ├── registry.py           # StrategyConfig, StrategyRegistry
│   │   └── errors.py             # SchemaValidationError
│   │
│   ├── services/
│   │   ├── aggregator.py         # calculate_weighted_return, merge_equity_curves,
│   │   │                         #   calculate_combined_drawdown
│   │   ├── cache.py              # get_cached, set_cached, invalidate_key, invalidate_pattern
│   │   ├── cache_invalidator.py  # invalidate_overall_cache, invalidate_strategy_cache, flush_all
│   │   ├── ingestion.py          # persist_daily_report, _payload_to_row
│   │   ├── snapshot_writer.py    # maybe_write_snapshot, _compute_aggregates, _extract_equity_curve
│   │   ├── strategy_registry.py  # load_registry, get_registry, set_registry, clear_registry
│   │   ├── performance.py        # compute_overall_performance, compute_strategy_performance,
│   │   │                         #   compute_strategy_performance_range
│   │   ├── portfolio.py          # query_latest_snapshot, query_snapshot_by_date,
│   │   │                         #   compute_portfolio_equity_curve
│   │   └── errors.py             # ServiceError, CacheError, AggregationError, etc.
│   │
│   └── db/
│       ├── postgres.py           # asyncpg.Pool singleton (get_pool / close_pool)
│       ├── mongo.py              # motor AsyncIOMotorClient singleton (get_client / close_client)
│       └── redis_client.py       # redis.asyncio.Redis singleton (get_redis / close_redis)
│
├── tests/
│   ├── conftest.py               # set_env, async_client, mock_pool, load_test_registry
│   ├── integration/
│   │   ├── conftest.py           # integration marker auto-tag, integration_client fixture
│   │   └── test_end_to_end.py    # 7 end-to-end tests
│   ├── api/v1/                   # Per-endpoint test files
│   ├── schemas/                  # Schema validation tests
│   ├── services/                 # Service-layer unit tests
│   └── db/                       # DB singleton tests
│
├── Dockerfile                    # Multi-stage (builder + runtime), non-root appuser, HEALTHCHECK
├── docker-compose.yml            # api-gateway + redis on quant-network
├── pyproject.toml                # Dependencies, tool config (ruff, mypy, pytest)
├── strategies.json               # Strategy registry (csm-set-01 by default)
├── .env.example                  # Environment template
└── docs/
    ├── PROJECT.md                # This file
    └── plans/ROADMAP.md          # Phased build-out roadmap
```

---

## 3. Configuration (`src/config.py`)

`Settings` is a Pydantic v2 model via `pydantic-settings.BaseSettings`. All values
come from environment variables or `.env`. Access via `get_settings()` (lazy, cached).

| Field | Type | Default | Description |
|---|---|---|---|
| `postgres_dsn` | `str` | (required) | PostgreSQL DSN for `db_gateway` |
| `mongo_uri` | `str` | (required) | MongoDB URI |
| `redis_url` | `str` | (required) | Redis URL |
| `csm_set_service_url` | `str` | (required) | Base URL of CSM-SET strategy service |
| `internal_api_key` | `str` | (required, min_length=1) | Shared secret for X-API-Key header |
| `log_level` | `str` | `"INFO"` | Python logging level |
| `strategy_registry_path` | `Path` | `Path("strategies.json")` | Path to registry JSON |
| `overall_performance_ttl_seconds` | `int` | `300` | Cache TTL for overall_performance key |
| `strategy_performance_ttl_seconds` | `int` | `300` | Cache TTL for strategy:{id}:performance |
| `portfolio_snapshot_ttl_seconds` | `int` | `3600` | Cache TTL for portfolio_snapshot:{date} |

---

## 4. Database Layer (`src/db/`)

### PostgreSQL (`src/db/postgres.py`)
- Lazy singleton `asyncpg.Pool`
- `get_pool() -> asyncpg.Pool` — creates on first call, reuses thereafter
- `close_pool() -> None` — closes and nulls the pool

### Redis (`src/db/redis_client.py`)
- Lazy singleton `redis.asyncio.Redis` with `decode_responses=True`
- `get_redis() -> aioredis.Redis` — creates on first call
- `close_redis() -> None` — closes (uses `aclose()` in redis-py 5.0+)

### MongoDB (`src/db/mongo.py`)
- Lazy singleton `motor.motor_asyncio.AsyncIOMotorClient`
- Not yet used by any endpoint (reserved for future document storage)

### Database tables (provisioned by `quant-infra-db`)

**`daily_performance`:**
| Column | Type | Description |
|---|---|---|
| `strategy_id` | `TEXT` | Strategy identifier |
| `total_value` | `DOUBLE PRECISION` | Total portfolio value |
| `daily_return` | `DOUBLE PRECISION` | Computed as daily_pnl / total_value |
| `max_drawdown` | `DOUBLE PRECISION` | Maximum drawdown (negative) |
| `sharpe_ratio` | `DOUBLE PRECISION` | Sharpe ratio |
| `time` | `TIMESTAMPTZ` | Report timestamp (UTC) |
| `metadata` | `JSONB` | Raw daily_pnl + equity_curve |

**`portfolio_snapshot`:**
| Column | Type | Description |
|---|---|---|
| `time` | `TIMESTAMPTZ` | Snapshot date (midnight UTC) |
| `total_portfolio` | `DOUBLE PRECISION` | Sum of all strategy values |
| `weighted_return` | `DOUBLE PRECISION` | Capital-weighted daily return |
| `combined_drawdown` | `DOUBLE PRECISION` | Portfolio-level max drawdown |
| `active_strategies` | `INTEGER` | Count of active strategies |
| `allocation` | `JSONB` | strategy_id → weight mapping |

---

## 5. Schemas (`src/schemas/`)

### Input (`strategy.py`)

**`StrategyPayload`** — what Strategy Services POST:
```python
class StrategyPayload(BaseModel, frozen=True, str_strip_whitespace=True):
    strategy_metadata: StrategyMetadata
    performance_metrics: PerformanceMetrics
    current_exposure: CurrentExposure
    extended_data: dict[str, object] = {}
```

**`StrategyMetadata`** — `id: str`, `type: str`, `last_updated: datetime` (UTC enforced)

**`PerformanceMetrics`** — `daily_pnl: Decimal`, `equity_curve: list[EquityPoint]` (min_length=1),
`max_drawdown: Decimal` (must be ≤0), `sharpe_ratio: Decimal`

**`CurrentExposure`** — `total_value: Decimal` (ge=0), `cash_balance: Decimal` (ge=0),
`positions_count: int` (ge=0)

**`EquityPoint`** — `date: str` (pattern `^\d{4}-\d{2}-\d{2}$`), `value: Decimal`

All datetime fields must be timezone-aware and UTC. All Decimal fields have explicit
`max_digits` and `decimal_places`.

### Output (`gateway.py`)

**`StrategyPerformanceResponse`:**
```python
class StrategyPerformanceResponse(BaseModel, frozen=True, str_strip_whitespace=True):
    strategy_id: str
    daily_pnl: Decimal           # max_digits=18, decimal_places=4
    total_value: Decimal         # max_digits=18, decimal_places=4, ge=0
    max_drawdown: Decimal        # max_digits=8, decimal_places=4
    sharpe_ratio: Decimal        # max_digits=8, decimal_places=4
    last_updated: datetime       # UTC enforced
```

**`OverallPerformanceResponse`:**
```python
class OverallPerformanceResponse(BaseModel, frozen=True):
    total_portfolio_value: Decimal    # max_digits=18, decimal_places=4, ge=0
    weighted_daily_return: Decimal    # max_digits=8, decimal_places=6
    combined_max_drawdown: Decimal    # max_digits=8, decimal_places=4
    active_strategies: int            # ge=0
    allocation: dict[str, Decimal]    # strategy_id → weight
    strategies: list[StrategyPerformanceResponse]
    computed_at: datetime             # UTC enforced
```

**`PortfolioSnapshotResponse`:**
```python
class PortfolioSnapshotResponse(BaseModel, frozen=True):
    snapshot_date: date
    total_portfolio_value: Decimal    # max_digits=18, decimal_places=4, ge=0
    weighted_daily_return: Decimal    # max_digits=8, decimal_places=6
    combined_drawdown: Decimal | None # max_digits=8, decimal_places=4
    active_strategies: int            # ge=0
    allocation: dict[str, Decimal]
    computed_at: datetime             # UTC
```

### Registry (`registry.py`)

```python
class StrategyConfig(BaseModel, frozen=True, str_strip_whitespace=True):
    id: str                   # min_length=1
    name: str                 # min_length=1
    service_url: str          # min_length=1
    capital_weight: Decimal   # ge=0, max_digits=8, decimal_places=4
    active: bool = True

class StrategyRegistry(BaseModel, frozen=True):
    strategies: list[StrategyConfig]
    def active_strategies() -> list[StrategyConfig]
    def by_id(strategy_id: str) -> StrategyConfig | None
```

---

## 6. Service Layer (`src/services/`)

### Aggregation Engine (`aggregator.py`)
Pure functions — no I/O, no async.

**`calculate_weighted_return(strategies, weights) -> float`**
- Formula: `Σ (daily_pnl_i / total_value_i) × weight_i / Σ weights`
- Excludes strategies with `total_value <= 0`
- Returns `0.0` when `sum(weights) <= 0`

**`merge_equity_curves(curves, weights, normalize=True) -> list[EquityPoint]`**
- Drops empty curves, zero-weight strategies, and curves with first value ≤ 0
- When `normalize=True`: normalizes each curve to base 100 (divide by first value × 100)
- When `normalize=False`: uses raw cumulative values as-is
- Outer-joins on date strings; forward-fills missing dates
- Per-row weighted sum across strategies with data on that row

**`calculate_combined_drawdown(curves, weights) -> float`**
- Calls `merge_equity_curves` internally
- Single-pass O(n) scan: `min(value / running_peak - 1)`
- Returns negative value (matching max_drawdown convention) or `0.0`

### Caching (`cache.py`)
**`get_cached(key, model_type) -> T | None`**
- Fetches JSON from Redis, validates with `model_validate()`, returns model or None
- Returns None on miss, corrupt JSON, or validation failure
- Raises `CacheError` on Redis failure

**`set_cached(key, value, ttl) -> None`**
- Serializes Pydantic model via `model_dump_json()`, stores with SETEX

**`invalidate_key(key) -> None`** — DEL single key

**`invalidate_pattern(pattern) -> int`** — SCAN + DELETE, returns count deleted

### Cache Invalidator (`cache_invalidator.py`)
- **Key constants:** `OVERALL_PERFORMANCE_KEY = "overall_performance"`, `STRATEGY_PERFORMANCE_PREFIX = "strategy:"`, `STRATEGY_PERFORMANCE_SUFFIX = ":performance"`
- `invalidate_overall_cache()` — best-effort, never raises
- `invalidate_strategy_cache(strategy_id)` — best-effort, never raises
- `flush_all()` — deletes all `gateway:*` keys, propagates errors

### Performance Service (`performance.py`)
**`compute_overall_performance(pool, registry) -> OverallPerformanceResponse`**
- Queries latest row per active strategy from `daily_performance`
- Computes weighted return, combined drawdown, allocation
- Zero-valued fields when no active strategies or no rows exist

**`compute_strategy_performance(pool, strategy_id) -> StrategyPerformanceResponse`**
- Queries latest `daily_performance` row for a single strategy
- Raises `ServiceError` when no row exists

**`compute_strategy_performance_range(pool, strategy_id, from_date, to_date) -> list[StrategyPerformanceResponse]`**
- Queries `daily_performance` rows in date range (inclusive)
- Returns empty list when no rows match (not an error)
- Ordered by `time ASC`

### Portfolio Service (`portfolio.py`)
**`query_latest_snapshot(pool) -> PortfolioSnapshotResponse | None`**

**`query_snapshot_by_date(pool, snapshot_date) -> PortfolioSnapshotResponse | None`**

**`compute_portfolio_equity_curve(pool, registry, normalize=True) -> list[EquityPoint]`**
- Reads latest equity curves from all active strategies
- Calls `merge_equity_curves` with appropriate `normalize` flag

### Strategy Registry (`strategy_registry.py`)
- `load_registry(path) -> StrategyRegistry` — loads and validates JSON
- `set_registry(registry)` / `clear_registry()` / `get_registry() -> StrategyRegistry`
- Module-global singleton, set at startup, cleared at shutdown

### Ingestion (`ingestion.py`)
**`persist_daily_report(payload, pool) -> None`**
- Converts `StrategyPayload` to a `daily_performance` row
- Computes `daily_return = daily_pnl / total_value`
- Computes `cumulative_return` from first/last equity curve points
- INSERT with ON CONFLICT for idempotent upsert
- Stores raw `daily_pnl` and `equity_curve` in metadata JSONB

### Snapshot Writer (`snapshot_writer.py`)
**`maybe_write_snapshot(pool, registry, now=None) -> bool`**
- Checks if every active strategy has reported today
- If round-complete: computes aggregates, upserts `portfolio_snapshot`, calls cache invalidation
- Returns True if snapshot was written

### Error types (`errors.py`)
```
ServiceError
├── CacheError
├── AggregationError
├── StrategyRegistryLoadError
├── UnknownStrategyError
└── IngestionPersistError
```

---

## 7. API Endpoints

All endpoints are mounted under `/api/v1`. OpenAPI docs at `/docs` and `/redoc`.

| Method | Path | Auth | Description | Cache | Response Model |
|--------|------|------|-------------|-------|----------------|
| GET | `/health` | — | Liveness probe | — | `{"status": "ok"}` |
| POST | `/api/v1/ingest/daily-report` | `X-API-Key` | Ingest daily performance report | — | `{"status": "accepted", ...}` |
| GET | `/api/v1/overall-performance` | — | Aggregated portfolio performance | 300s | `OverallPerformanceResponse` |
| GET | `/api/v1/strategies` | — | List all active strategies | — | `list[StrategyConfig]` |
| GET | `/api/v1/strategies/{id}` | — | Single strategy detail | — | `StrategyConfig` |
| GET | `/api/v1/strategies/{id}/performance` | — | Latest or date-range performance | 300s (latest only) | `StrategyPerformanceResponse \| list[StrategyPerformanceResponse]` |
| GET | `/api/v1/strategies/{id}/equity-curve` | — | Full equity curve from metadata | — | `list[EquityPoint]` |
| GET | `/api/v1/portfolio/snapshot` | — | Latest portfolio snapshot | 3600s | `PortfolioSnapshotResponse` |
| GET | `/api/v1/portfolio/snapshot/{date}` | — | Snapshot for specific date | 3600s | `PortfolioSnapshotResponse` |
| GET | `/api/v1/portfolio/equity-curve` | — | Merged portfolio equity curve | — | `list[EquityPoint]` |
| POST | `/api/v1/admin/cache/flush` | `X-API-Key` | Flush all gateway cache keys | — | `{"status": "flushed", ...}` |

### Query parameters

**`GET /api/v1/strategies/{id}/performance`:**
- `?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD` — date-range query, returns list, uncached
- Without params — latest snapshot, cached (TTL 300s)
- Only one param → `422` with message "Both from_date and to_date are required for range queries"

**`GET /api/v1/portfolio/equity-curve`:**
- `?normalize=true` (default) — base-100 normalized curves
- `?normalize=false` — raw cumulative values

### Error responses

| Status | When |
|--------|------|
| 403 | Missing or wrong `X-API-Key` on authed endpoints |
| 404 | Unknown strategy ID or no data for date/strategy |
| 422 | Malformed JSON, validation failure, or partial date-range params |
| 500 | Database/Redis failure (typed error detail) |

### v2 engines — execution proxy (`src/api/v2/engines/execution.py`)

Thin reverse proxy to the standalone `quant-execution-engine` (in-network
`http://quant-execution-engine:8000`). The gateway holds **no broker
credential** — it forwards the caller's `X-API-Key`, passes the engine's typed
4xx rejection envelopes through verbatim, and maps transport failures to
`502/503/504`. The engine's `/admin/*` (kill-switch) surface is **never**
proxied. Mounted under `/api/v2/engines/execution`.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v2/engines/execution/health` | — | Engine liveness (stage + public_mode) |
| GET | `/api/v2/engines/execution/capabilities` | — | Per-(broker, market) capability matrix |
| POST | `/api/v2/engines/execution/orders` | `X-API-Key` | Submit a NormalizedOrder (idempotent on `client_order_id`) |
| GET | `/api/v2/engines/execution/orders/stream` | `X-API-Key` | **SSE** order-update event stream (see buffering note) |
| GET | `/api/v2/engines/execution/orders/{client_order_id}` | — | Read one order's normalized state |
| PATCH | `/api/v2/engines/execution/orders/{client_order_id}` | `X-API-Key` | Amend a resting order's price/quantity (native or cancel+replace) |
| DELETE | `/api/v2/engines/execution/orders/{client_order_id}` | `X-API-Key` | Cancel a resting order |
| GET | `/api/v2/engines/execution/order-book/{symbol}` | — | Order-book snapshot (JSON) |
| GET | `/api/v2/engines/execution/order-book/{symbol}/stream` | — | **SSE** order-book update stream (`?market=` required; see buffering note) |

**Buffering note:** the two SSE routes (`/orders/stream`,
`/order-book/{symbol}/stream`) stream the upstream body **unbuffered** as
`text/event-stream` (chunked transfer; `Cache-Control: no-cache`,
`X-Accel-Buffering: no`). The per-stream httpx read timeout is disabled so an
idle keep-alive-only stream is not killed. The `Last-Event-ID` request header
and all query params are forwarded; a non-200 upstream (e.g. 503
`order_stream_unavailable`, 404 `order_book_unavailable`) is buffered and
returned verbatim as JSON. Every other route stays buffered JSON.

---

## 8. Application Lifecycle (`src/main.py`)

1. **Startup:** `get_settings()` → `configure_logging(settings)` → load strategy registry → open asyncpg pool → open Redis connection
2. **Each request:** `RequestIDMiddleware` generates `uuid4`, sets `request_id_var` ContextVar, attaches `X-Request-ID` response header
3. **Shutdown:** close pool → close Redis → clear registry

### Request-ID Middleware
- Generates `uuid4` per request
- Stores on `request.state.request_id` and in `request_id_var` ContextVar
- Attaches `X-Request-ID` to every response
- Structured log formatter includes `request_id` when available

### Structured Logging (`src/logging_config.py`)
- Custom `JSONFormatter(logging.Formatter)` emits one-line JSON records
- Every record: `timestamp` (UTC ISO-8601), `level`, `logger`, `message`
- Optional: `request_id` (from ContextVar), `exc_info` (on exception)
- Installed as sole root handler via `configure_logging(settings)` at startup
- Never logs request body, auth headers, or secrets

---

## 9. Docker & Deployment

### Dockerfile
- **Builder stage:** `python:3.11-slim`, copies `uv` from `ghcr.io/astral-sh/uv`, runs `uv sync --frozen --no-dev`
- **Runtime stage:** `python:3.11-slim`, installs `curl` for healthcheck, creates non-root `appuser` (uid 1000), copies venv + src + strategies.json
- **ENV:** `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`
- **HEALTHCHECK:** `curl -f http://localhost:8000/health || exit 1` (interval 30s, timeout 10s, retries 3, start_period 10s)
- **USER:** `appuser`
- **EXPOSE:** `8000`

### docker-compose.yml
- Two services: `api-gateway` (builds locally) + `redis` (redis:7-alpine)
- Network: external `quant-network`
- Gateway depends on Redis with `condition: service_healthy`
- Gateway publishes to `${API_GATEWAY_HOST_PORT:-8000}:8000`
- **Redis healthcheck:** `redis-cli ping` (interval 10s, timeout 5s, retries 5)
- **Gateway healthcheck:** `curl -f http://localhost:8000/health` (interval 30s, timeout 10s, retries 3, start_period 40s)

---

## 10. Quality Gate

Run before every commit:

```bash
uv run ruff check .        # Zero findings required
uv run ruff format --check .  # No formatting drift
uv run mypy src tests      # Strict mode (strict=true), zero errors
uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=90
```

Integration tests (require running infrastructure):

```bash
uv run pytest -m integration -v
```

### Tool configuration (`pyproject.toml`)
- **ruff:** line-length=100, select E/F/I/UP/B/SIM, ignore B008
- **mypy:** strict=true, python_version=3.11, ignore_missing_imports=true
- **pytest:** asyncio_mode=auto, addopts include `-m "not integration"` (exclude integration from default run), markers: `integration`
- **coverage:** branch=true, source=src, fail-under=90

---

## 11. Caching Strategy

### Cache keys
| Key | Value | TTL |
|---|---|---|
| `overall_performance` | `OverallPerformanceResponse` JSON | 300 s |
| `strategy:{id}:performance` | `StrategyPerformanceResponse` JSON | 300 s |
| `portfolio_snapshot:latest` | `PortfolioSnapshotResponse` JSON | 3600 s |
| `portfolio_snapshot:{YYYY-MM-DD}` | `PortfolioSnapshotResponse` JSON | 3600 s |

### Cache behavior
- **Cache-aside pattern:** check cache → on miss query Postgres → compute → write cache → return
- **Graceful degradation:** `CacheError` on `set_cached` is caught and logged; response still returned
- **Invalidation:** called after successful snapshot upsert; best-effort, never blocks
- **Flush:** `POST /api/v1/admin/cache/flush` does SCAN + DELETE for all `gateway:*` keys

### Endpoints NOT cached
- Equity curve endpoints (data can be large, infrequently accessed)
- Strategy detail (registry is in-memory)
- Date-range queries (return historical lists, no cache key defined)

---

## 12. Data Flow: Ingestion to Dashboard

```
quant-csm-set (or any strategy service)
    │
    │ POST /api/v1/ingest/daily-report
    │ Header: X-API-Key
    │ Body: StrategyPayload (JSON)
    ▼
┌──────────────────────────────────────┐
│ quant-api-gateway                    │
│                                      │
│ 1. verify_api_key dependency         │
│ 2. Pydantic validates StrategyPayload│
│ 3. persist_daily_report() → Postgres │
│ 4. maybe_write_snapshot()            │
│    - Check if all strats reported    │
│    - Compute aggregates via          │
│      aggregator.calculate_*()        │
│    - Upsert portfolio_snapshot       │
│    - Invalidate cache keys           │
│    (best-effort, failures logged)    │
│                                      │
│ React Dashboard                      │
│    │ GET /api/v1/overall-performance │
│    ▼                                  │
│ 1. Check Redis cache                 │
│ 2. On miss: query Postgres           │
│ 3. Compute via services.performance  │
│ 4. Write cache, return response      │
└──────────────────────────────────────┘
```

---

## 13. Implementation History

| Phase | Branch | Date | Summary |
|---|---|---|---|
| 1 | `feat/phase-1-bootstrap` | 2026-05-14 | FastAPI skeleton, /health, Docker Compose, Settings |
| 2 | `feat/phase-2-data-models` | 2026-05-14 | Pydantic v2 schemas (input/output), DB connection layer |
| 3 | `feat/phase-3-strategy-ingestion` | 2026-05-14 | POST ingestion, strategy registry, snapshot writer |
| 4 | `feat/phase-4-aggregation-engine` | 2026-05-15 | Weighted return, combined drawdown, equity-curve merger |
| 5 | `feat/phase-5-redis-caching-layer` | 2026-05-15 | Redis cache-aside, cache invalidation, admin flush endpoint |
| 6 | `feat/phase-6-rest-api-endpoints` | 2026-05-15 | 7 read endpoints with cache-aside, portfolio/performance modules |
| 7 | `feat/phase-7-operations-quality-gate` | 2026-05-15 | JSON logging, request-ID middleware, Docker hardening, date-range querying, normalize=false, integration tests, README table, coverage ≥90% |

---

## 14. Hard Rules

1. **Always `uv run`** — never `python`, `pip`, `poetry`, or `conda` directly
2. **Async-first I/O** — all HTTP via `httpx.AsyncClient`; `requests` forbidden
3. **Pydantic at boundaries** — data crossing module boundaries must be a Pydantic model
4. **Type hints everywhere** — full annotations on all public functions, no bare `Any`
5. **≥90% test coverage** — enforced by `--cov-fail-under=90`
6. **No secrets in repo** — all config via env vars; `.env` is gitignored
7. **Ruff + mypy clean** before every commit
8. **Conventional Commits** — `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`
9. **Logging, not `print`** — structured JSON via `logging.getLogger(__name__)`
10. **Timestamps in UTC** internally; localize only at presentation boundaries

---

## 15. Key Design Decisions

- **Decimal for finance fields** — exact arithmetic; `max_digits`/`decimal_places` enforced
- **Frozen Pydantic models** — immutability prevents accidental mutation downstream
- **`str_strip_whitespace=True`** on string-bearing models
- **UTC enforced** on all datetime fields via `@field_validator`
- **Cache stores Pydantic models** — `model_dump_json()` → `model_validate()` round-trip
- **SCAN not KEYS** for cache pattern invalidation (non-blocking)
- **Best-effort cache invalidation** — cache is a performance optimization, correctness comes from Postgres
- **No auth on read endpoints** — the Dashboard is public; gateway sits behind Docker networking
- **Non-root container user** — `appuser` uid 1000 limits blast radius
- **`contextvars` for request-ID** — correct with asyncio task scheduling
- **Integration tests gated** with `-m integration` — require running infrastructure
- **pandas only for equity-curve date alignment** — all computation is pure Python/Decimal
