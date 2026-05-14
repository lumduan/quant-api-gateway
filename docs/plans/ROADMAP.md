# quant-api-gateway — Roadmap

The `quant-api-gateway` project is the **Central Aggregator Service** of the
Quant Trading System. It ingests Daily Performance reports from every Strategy
Service (currently `quant-csm-set`), computes weighted return and combined
drawdown across strategies, caches the result in Redis, and exposes a versioned
REST API that the React Dashboard and any other client can read from.

The service runs as a FastAPI container on the shared Docker network
`quant-network`, alongside the `quant-infra-db` stack (`quant-postgres`,
`quant-mongo`). It depends on `quant-infra-db` for storage and on every
upstream Strategy Service for ingestion.

---

## Status legend

| Symbol | Meaning |
|---|---|
| `[ ]` | Not started |
| `[~]` | In progress |
| `[x]` | Complete |
| `[-]` | Skipped / deferred |

---

## Phase 1 — Project Bootstrap 🏗️ ✅ (completed 2026-05-14)

> **Goal:** Stand up the FastAPI project skeleton with Docker Compose so that
> `docker compose up -d` works on a fresh clone, the container joins
> `quant-network`, and `GET /health` returns `200 OK`.

**Status:** Complete. See [`phase_1_bootstrap/phase_1_bootstrap.md`](phase_1_bootstrap/phase_1_bootstrap.md) for the implementation plan and post-implementation notes.

### 1.1 Project structure

- [x] Create the project folder `quant-api-gateway/`
- [x] Create `README.md` describing the overview, setup steps, and API endpoints
- [x] Create `.env.example`:
  ```env
  POSTGRES_DSN=postgresql://postgres:<pass>@quant-postgres:5432/db_gateway
  MONGO_URI=mongodb://quant-mongo:27017/
  REDIS_URL=redis://quant-redis:6379/0
  CSM_SET_SERVICE_URL=http://quant-csm-set:8001
  INTERNAL_API_KEY=your_strong_internal_key_here
  LOG_LEVEL=INFO
  ```
- [x] Create `.gitignore`:
  - Do not commit the real `.env`
  - Do not commit `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`
- [x] Create `pyproject.toml` with the core dependencies:
  ```toml
  [project]
  name = "quant-api-gateway"
  version = "0.1.0"
  requires-python = ">=3.11"
  dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "asyncpg>=0.29",
    "motor>=3.4",
    "redis[asyncio]>=5.0",
    "pydantic>=2.7",
    "pydantic-settings>=2.2",
    "httpx>=0.27",
  ]

  [dependency-groups]
  dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5",
    "ruff>=0.6",
    "mypy>=1.10",
  ]
  ```

**Exit criteria:** project skeleton is complete; no real credentials are
present in the repository.

### 1.2 FastAPI application bootstrap

- [x] Create `src/main.py` — FastAPI app instance with an async lifespan:
  ```python
  from contextlib import asynccontextmanager
  from collections.abc import AsyncIterator

  from fastapi import FastAPI


  @asynccontextmanager
  async def lifespan(app: FastAPI) -> AsyncIterator[None]:
      """Open database, Redis, and HTTP connections on startup; close on shutdown."""
      await startup()
      try:
          yield
      finally:
          await shutdown()


  app = FastAPI(
      title="Quant API Gateway",
      version="1.0.0",
      lifespan=lifespan,
  )
  ```
- [x] Create `src/config.py` — Pydantic Settings:
  ```python
  from pydantic_settings import BaseSettings, SettingsConfigDict


  class Settings(BaseSettings):
      postgres_dsn: str
      mongo_uri: str
      redis_url: str
      csm_set_service_url: str
      internal_api_key: str
      log_level: str = "INFO"

      model_config = SettingsConfigDict(env_file=".env", extra="ignore")


  settings = Settings()  # type: ignore[call-arg]
  ```
- [x] Create `src/api/v1/router.py` — top-level router that mounts every
  sub-router (ingest, performance, strategies, portfolio)
- [x] Endpoint: `GET /health` → `{"status": "ok"}`
- [x] Verify locally:
  ```bash
  uv run uvicorn src.main:app --reload
  curl -s localhost:8000/health   # → {"status":"ok"}
  ```

**Exit criteria:** FastAPI starts cleanly; `/health` returns `200 OK`.

### 1.3 Docker Compose on `quant-network`

- [x] Create `Dockerfile` (uv-native, Python 3.12-slim):
  ```dockerfile
  FROM python:3.12-slim
  WORKDIR /app

  RUN pip install --no-cache-dir uv

  COPY pyproject.toml uv.lock* ./
  RUN uv sync --frozen --no-dev || uv sync --no-dev

  COPY src/ ./src/
  EXPOSE 8000
  CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
  ```
- [x] Create `docker-compose.yml`:
  ```yaml
  services:
    api-gateway:
      build: .
      container_name: quant-api-gateway
      restart: always
      ports:
        - "8000:8000"
      env_file: .env
      depends_on:
        redis:
          condition: service_healthy
      healthcheck:
        test: ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval: 30s
        timeout: 10s
        retries: 3
        start_period: 15s

    redis:
      image: redis:7-alpine
      container_name: quant-redis
      restart: always
      ports:
        - "6379:6379"
      volumes:
        - redis_data:/data
      healthcheck:
        test: ["CMD", "redis-cli", "ping"]
        interval: 30s
        timeout: 10s
        retries: 3

  volumes:
    redis_data:

  networks:
    default:
      name: quant-network
      external: true
  ```
- [x] Ensure `quant-network` exists (created once by `quant-infra-db`); if
  not, run `docker network create quant-network`
- [x] Verify:
  ```bash
  docker compose up -d
  docker compose ps      # api-gateway + redis both (healthy)
  ```

**Exit criteria:** both containers are on `quant-network`, hostnames
`quant-api-gateway` and `quant-redis` resolve, and `docker compose ps` reports
`(healthy)` for both.

---

## Phase 2 — Data Models & Schema Validation 📐 ✅ (completed 2026-05-14)

> **Goal:** Define Pydantic v2 models that exactly match the Standard JSON
> emitted by `quant-csm-set`, so every payload entering or leaving the gateway
> is validated at the boundary.
>
> **Status:** Complete. See [`phase_2_data_models/phase_2_data_models.md`](phase_2_data_models/phase_2_data_models.md) for the implementation plan and post-implementation notes.

### 2.1 Input schema (from Strategy Services)

- [x] Create `src/schemas/strategy.py`:
  ```python
  from datetime import datetime

  from pydantic import BaseModel, Field


  class StrategyMetadata(BaseModel):
      id: str
      type: str
      last_updated: datetime


  class EquityPoint(BaseModel):
      date: str
      value: float


  class PerformanceMetrics(BaseModel):
      daily_pnl: float
      equity_curve: list[EquityPoint]
      max_drawdown: float
      sharpe_ratio: float


  class CurrentExposure(BaseModel):
      total_value: float
      cash_balance: float
      positions_count: int


  class StrategyPayload(BaseModel):
      """Standard JSON contract that every Strategy Service POSTs to the gateway."""

      strategy_metadata: StrategyMetadata
      performance_metrics: PerformanceMetrics
      current_exposure: CurrentExposure
      extended_data: dict[str, object] = Field(default_factory=dict)
  ```
- [x] Verify: a payload with missing fields → `422 Unprocessable Entity` with
  a descriptive error body

**Exit criteria:** Pydantic validates every field automatically; malformed
input is rejected at the boundary before it reaches any service logic.

### 2.2 Output schema (to the Dashboard)

- [x] Create `src/schemas/gateway.py`:
  ```python
  from datetime import datetime

  from pydantic import BaseModel


  class StrategyPerformanceResponse(BaseModel):
      strategy_id: str
      daily_pnl: float
      total_value: float
      max_drawdown: float
      sharpe_ratio: float
      last_updated: datetime


  class OverallPerformanceResponse(BaseModel):
      total_portfolio_value: float
      weighted_daily_return: float
      combined_max_drawdown: float
      active_strategies: int
      allocation: dict[str, float]  # strategy_id → weight
      strategies: list[StrategyPerformanceResponse]
      computed_at: datetime
  ```
- [x] Verify: serialized responses emit `datetime` as ISO 8601 and contain no
  stray fields beyond the schema

**Exit criteria:** every output model is complete; every response includes a
`computed_at` timestamp.

### 2.3 Database layer — asyncpg + motor + redis.asyncio

> Note: the `daily_performance` and `portfolio_snapshot` tables live in
> `db_gateway`, which is provisioned by `quant-infra-db`. The gateway only
> reads from and writes to those tables — it does not own the schema.

- [x] Create `src/db/postgres.py` — asyncpg connection pool getter:
  ```python
  import asyncpg

  from src.config import settings

  _pool: asyncpg.Pool | None = None


  async def get_pool() -> asyncpg.Pool:
      """Return the lazily-initialized asyncpg pool for `db_gateway`."""
      global _pool
      if _pool is None:
          _pool = await asyncpg.create_pool(settings.postgres_dsn)
      return _pool


  async def close_pool() -> None:
      global _pool
      if _pool is not None:
          await _pool.close()
          _pool = None
  ```
- [x] Create `src/db/mongo.py` — motor async client getter:
  ```python
  from motor.motor_asyncio import AsyncIOMotorClient

  from src.config import settings

  _client: AsyncIOMotorClient | None = None


  def get_client() -> AsyncIOMotorClient:
      """Return the singleton motor client for MongoDB."""
      global _client
      if _client is None:
          _client = AsyncIOMotorClient(settings.mongo_uri)
      return _client


  def close_client() -> None:
      global _client
      if _client is not None:
          _client.close()
          _client = None
  ```
- [x] Create `src/db/redis_client.py` — `redis.asyncio` connection getter:
  ```python
  import redis.asyncio as aioredis

  from src.config import settings

  _redis: aioredis.Redis | None = None


  async def get_redis() -> aioredis.Redis:
      """Return the singleton redis.asyncio connection."""
      global _redis
      if _redis is None:
          _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
      return _redis


  async def close_redis() -> None:
      global _redis
      if _redis is not None:
          await _redis.close()
          _redis = None
  ```
- [x] Verify connectivity from inside the container against `quant-postgres`,
  `quant-mongo`, and `quant-redis` (all by hostname)

**Exit criteria:** PostgreSQL, MongoDB, and Redis can all be reached from the
FastAPI container via their `quant-network` hostnames.

---

## Phase 3 — Strategy Ingestion & Data Storage 📥

> **Goal:** Accept Daily Performance reports from every Strategy Service,
> validate them, and persist them into `db_gateway`.

### 3.1 Ingestion endpoint

- [ ] Create `src/api/v1/ingest.py`:
  - `POST /api/v1/ingest/daily-report` — Strategy Service pushes a
    `StrategyPayload`
  - Validate via the `StrategyPayload` schema
  - Insert into `daily_performance` in `db_gateway`
- [ ] Add API-key authentication for the endpoint:
  ```python
  from fastapi import Depends, HTTPException, status
  from fastapi.security import APIKeyHeader

  from src.config import settings

  _api_key_header = APIKeyHeader(name="X-API-Key")


  async def verify_api_key(key: str = Depends(_api_key_header)) -> None:
      """Reject requests that do not present the internal API key."""
      if key != settings.internal_api_key:
          raise HTTPException(
              status_code=status.HTTP_403_FORBIDDEN,
              detail="Invalid API key",
          )
  ```
- [ ] Verify: a valid `StrategyPayload` from `quant-csm-set` → `201 Created`
  → a corresponding row appears in `daily_performance`
- [ ] Verify: a request without `X-API-Key` → `403 Forbidden`

**Exit criteria:** the gateway accepts `StrategyPayload` from `quant-csm-set`
and persists rows in `daily_performance`.

### 3.2 Strategy registry

- [ ] Create `src/services/strategy_registry.py` — config-driven registry
  loaded from `strategies.json`:
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
- [ ] Load the registry on application startup (inside `lifespan`)
- [ ] Endpoint: `GET /api/v1/strategies` — return every active strategy

**Exit criteria:** adding a new strategy is a JSON edit; no code change
required.

### 3.3 Portfolio snapshot writer

- [ ] Create `src/services/snapshot_writer.py`:
  - Aggregate every active strategy's latest report
  - Insert a daily `portfolio_snapshot` row (total, weighted_return,
    allocation)
- [ ] Invoke the writer after a full ingestion round (every active strategy
  has reported for the day)
- [ ] Verify: two strategies report on the same day → one new
  `portfolio_snapshot` row with `active_strategies = 2`

**Exit criteria:** `portfolio_snapshot` is updated automatically once every
active strategy has reported.

---

## Phase 4 — Aggregation Engine 🧮

> **Goal:** Compute weighted return, combined drawdown, and a merged equity
> curve from multiple strategies.

### 4.1 Weighted return

- [ ] Create `src/services/aggregator.py`:
  ```python
  from src.schemas.gateway import StrategyPerformanceResponse


  def calculate_weighted_return(
      strategies: list[StrategyPerformanceResponse],
      weights: dict[str, float],
  ) -> float:
      """Compute the capital-weighted daily return.

      Formula:
          Σ (daily_pnl_i / total_value_i) × weight_i   /   Σ weights

      Args:
          strategies: Latest performance snapshot for every active strategy.
          weights: Map of strategy_id → capital weight.

      Returns:
          Weighted daily return in fractional form (e.g. 0.0148 == 1.48%).
      """
      total_weight = sum(weights.values())
      if total_weight <= 0:
          return 0.0
      weighted = sum(
          (s.daily_pnl / s.total_value) * weights.get(s.strategy_id, 0.0)
          for s in strategies
          if s.total_value > 0
      )
      return weighted / total_weight
  ```
- [ ] Unit test: two strategies (60/40 weighting) → known expected return
- [ ] Unit test edge cases: all weights zero, single strategy,
  `total_value == 0`

**Exit criteria:** `calculate_weighted_return` passes unit tests for every
edge case.

### 4.2 Combined drawdown

- [ ] Add `calculate_combined_drawdown()` to `aggregator.py`:
  - Take equity curves from every active strategy
  - Merge them into a single portfolio equity curve (weighted sum)
  - Return the max drawdown of the merged curve: `(peak − trough) / peak`
- [ ] Unit test: a hand-crafted equity curve with a known drawdown → exact
  match

**Exit criteria:** combined drawdown is correct on known fixtures and
gracefully handles missing data for individual strategies.

### 4.3 Equity-curve merger

- [ ] Add `merge_equity_curves()` to `aggregator.py`:
  - Align points by date (outer join)
  - Forward-fill missing dates
  - Normalize each input curve to base 100 before merging
- [ ] Unit test: two curves that span different date ranges → the merged
  curve covers every date

**Exit criteria:** the merger handles strategies that start trading on
different dates (e.g. CSM-SET today and a future TFEX strategy).

---

## Phase 5 — Redis Caching Layer ⚡

> **Goal:** Cache aggregated results in Redis so the Dashboard renders
> immediately and the service does not recompute on every request.

### 5.1 Cache layer

- [ ] Create `src/services/cache.py`:
  ```python
  import json
  from datetime import timedelta

  from src.db.redis_client import get_redis

  CACHE_TTL = timedelta(minutes=5)


  async def get_cached(key: str) -> dict[str, object] | None:
      """Return a cached value, or None on miss."""
      redis = await get_redis()
      value = await redis.get(key)
      return json.loads(value) if value else None


  async def set_cached(
      key: str,
      value: dict[str, object],
      ttl: timedelta = CACHE_TTL,
  ) -> None:
      """Set a cached value with a TTL."""
      redis = await get_redis()
      await redis.setex(
          key,
          int(ttl.total_seconds()),
          json.dumps(value, default=str),
      )
  ```
- [ ] Cache key conventions:
  - `overall_performance` — TTL 5 minutes
  - `strategy:{strategy_id}:performance` — TTL 5 minutes
  - `portfolio_snapshot:{YYYY-MM-DD}` — TTL 1 hour

**Exit criteria:** a cache miss recomputes; a cache hit responds in
< 10 ms.

### 5.2 Cache invalidation

- [ ] Create `src/services/cache_invalidator.py`:
  - `invalidate_overall_cache()` — called after every ingestion
  - `invalidate_strategy_cache(strategy_id)` — called when a single
    strategy updates
- [ ] Endpoint: `POST /api/v1/admin/cache/flush` — guarded by the internal
  API key, flushes every gateway-owned key
- [ ] Verify: a new ingestion request → the previous `overall_performance`
  cache entry is gone → the next request returns fresh data

**Exit criteria:** the Dashboard never sees stale data after a Strategy
Service posts a new report.

---

## Phase 6 — REST API Endpoints 🚦

> **Goal:** Expose every endpoint the React Dashboard and any third-party
> client needs.

### 6.1 Overall performance endpoint

- [ ] `GET /api/v1/overall-performance`:
  - Read active strategies from the registry
  - Query the latest row per strategy from `daily_performance`
  - Compute `weighted_daily_return` + `combined_max_drawdown` via the
    aggregator
  - Cache the result and return an `OverallPerformanceResponse`
  ```json
  {
    "total_portfolio_value": 1050000.00,
    "weighted_daily_return": 0.0148,
    "combined_max_drawdown": -6.3,
    "active_strategies": 1,
    "allocation": { "csm-set-01": 1.0 },
    "strategies": [],
    "computed_at": "2026-05-14T11:00:00Z"
  }
  ```
- [ ] Verify: data present → response returns every field with `200 OK`
- [ ] Verify: no data → `{"active_strategies": 0, …}` (never a 500)

**Exit criteria:** the endpoint responds in under 200 ms on a cache hit and
under 1 s on a cache miss.

### 6.2 Strategy-level endpoints

- [ ] `GET /api/v1/strategies` — list every active strategy
- [ ] `GET /api/v1/strategies/{strategy_id}` — single strategy detail
- [ ] `GET /api/v1/strategies/{strategy_id}/performance` — performance
  history; query params `?from=YYYY-MM-DD&to=YYYY-MM-DD`; sourced from
  `daily_performance`
- [ ] `GET /api/v1/strategies/{strategy_id}/equity-curve` — full equity
  curve
- [ ] Verify: unknown `strategy_id` → `404 Not Found` with a clear detail
  body

**Exit criteria:** every endpoint returns a correct HTTP status code and a
typed error body on failure.

### 6.3 Portfolio endpoints

- [ ] `GET /api/v1/portfolio/snapshot` — latest portfolio snapshot
- [ ] `GET /api/v1/portfolio/snapshot/{date}` — snapshot for a specific
  date (`YYYY-MM-DD`)
- [ ] `GET /api/v1/portfolio/equity-curve` — merged equity curve across
  every strategy; query params
  `?from=YYYY-MM-DD&to=YYYY-MM-DD&normalize=true`
- [ ] Verify: a date with no data → `404 Not Found`

**Exit criteria:** portfolio endpoints support full date-range querying.

### 6.4 API documentation

- [ ] Swagger UI live at `/docs`
- [ ] ReDoc live at `/redoc`
- [ ] Every endpoint sets `summary`, `description`, and `response_model`
- [ ] Record the endpoint reference table in `README.md`

**Exit criteria:** a new developer can open `/docs` and exercise the API
without reading source code.

---

## Phase 7 — Operations & Quality Gate ⚙️

> **Goal:** Be production-ready — structured logs, unit + integration tests,
> coverage, and a CI-ready quality gate.

### 7.1 Structured logging

- [ ] Create `src/logging_config.py` — JSON structured logging:
  ```python
  import json
  import logging
  from typing import Any


  class JSONFormatter(logging.Formatter):
      def format(self, record: logging.LogRecord) -> str:
          payload: dict[str, Any] = {
              "level": record.levelname,
              "message": record.getMessage(),
              "module": record.module,
              "timestamp": self.formatTime(record),
          }
          if record.exc_info:
              payload["exc_info"] = self.formatException(record.exc_info)
          return json.dumps(payload)


  def configure_logging(level: str = "INFO") -> None:
      handler = logging.StreamHandler()
      handler.setFormatter(JSONFormatter())
      root = logging.getLogger()
      root.handlers = [handler]
      root.setLevel(level)
  ```
- [ ] Log every request: `method`, `path`, `status_code`, `duration_ms`
- [ ] Log every cache hit / miss with the key
- [ ] Log every ingestion: `strategy_id`, payload size, duration

**Exit criteria:** every significant event is emitted as a single-line JSON
record that is trivially greppable in production.

### 7.2 Unit tests

- [ ] Create `tests/unit/test_aggregator.py`:
  - `test_weighted_return_two_strategies`
  - `test_weighted_return_single_strategy`
  - `test_combined_drawdown_known_curve`
  - `test_merge_equity_curves_with_date_gaps`
  - `test_zero_weight_strategy_excluded`
- [ ] Create `tests/unit/test_schemas.py`:
  - `test_strategy_payload_valid`
  - `test_strategy_payload_missing_required_field`
  - `test_overall_performance_response_serialization`
- [ ] Coverage target: hard gate ≥ 80% (`pyproject.toml`
  `--cov-fail-under=80`), with a Phase-7 stretch target of ≥ 90% for the
  unit suite specifically

**Exit criteria:** `uv run pytest tests/unit/ -v` is green.

### 7.3 Integration tests

- [ ] Create `tests/integration/test_endpoints.py` (using
  `httpx.AsyncClient`):
  - `test_health_endpoint` → `200 OK`
  - `test_ingest_valid_payload` → `201 Created`
  - `test_ingest_missing_api_key` → `403 Forbidden`
  - `test_ingest_invalid_payload` → `422 Unprocessable Entity`
  - `test_overall_performance_cached` — two consecutive requests; the
    second is a cache hit
  - `test_strategy_not_found` → `404 Not Found`
- [ ] Run integration tests against a real `db_gateway` and `quant-redis`
  (mark: `pytest -m integration`)
- [ ] Coverage target: ≥ 85% for the integration suite

**Exit criteria:** `uv run pytest tests/integration/ -v -m integration` is
green against a running `quant-network` stack.

### 7.4 Quality gate

- [ ] `uv run ruff check .` — no lint errors
- [ ] `uv run ruff format --check .` — no formatting drift
- [ ] `uv run mypy src tests` — no type errors (strict mode is on in
  `pyproject.toml`)
- [ ] `uv run pytest --cov=src --cov-report=term-missing` — coverage
  ≥ 80% global, ≥ 90% stretch
- [ ] Record the latest gate results in `README.md`

**Exit criteria:** the canonical project gate passes —
`uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest`.

---

## Project file structure

```
quant-api-gateway/
├── docker-compose.yml              # api-gateway + redis
├── Dockerfile                      # uv-native, python:3.12-slim
├── pyproject.toml                  # Dependencies + tool config
├── strategies.json                 # Strategy registry config
├── .env                            # Real credentials (gitignored)
├── .env.example                    # Credentials template
├── .gitignore
├── README.md                       # Overview, endpoints, setup
│
├── src/
│   ├── main.py                     # FastAPI app + lifespan
│   ├── config.py                   # Pydantic Settings
│   ├── logging_config.py           # JSON structured logging
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── router.py           # Mounts every sub-router
│   │       ├── ingest.py           # POST /api/v1/ingest/daily-report
│   │       ├── performance.py     # GET  /api/v1/overall-performance
│   │       ├── strategies.py       # GET  /api/v1/strategies/...
│   │       └── portfolio.py        # GET  /api/v1/portfolio/...
│   │
│   ├── schemas/
│   │   ├── strategy.py             # StrategyPayload (input)
│   │   └── gateway.py              # *Response models (output)
│   │
│   ├── services/
│   │   ├── aggregator.py           # Weighted return + combined drawdown
│   │   ├── strategy_registry.py    # Loads strategies.json
│   │   ├── snapshot_writer.py      # Writes portfolio_snapshot
│   │   ├── cache.py                # Redis get/set
│   │   └── cache_invalidator.py    # Invalidation logic
│   │
│   └── db/
│       ├── postgres.py             # asyncpg pool
│       ├── mongo.py                # motor client
│       └── redis_client.py         # redis.asyncio connection
│
└── tests/
    ├── unit/
    │   ├── test_aggregator.py
    │   └── test_schemas.py
    └── integration/
        └── test_endpoints.py
```

---

## Dependency Map

```
Phase 1 (Bootstrap + FastAPI + Docker Compose)
    └── Phase 2 (Data Models + DB Layer)
            └── Phase 3 (Strategy Ingestion + Storage)
                    └── Phase 4 (Aggregation Engine)
                            └── Phase 5 (Redis Caching)
                                    └── Phase 6 (REST API Endpoints)
                                            └── Phase 7 (Tests + Quality Gate)
                                                    └── [React Dashboard integration]
```

---

## External project dependencies

- **[quant-infra-db](https://github.com/lumduan/quant-infra-db)** — must be
  running first. Provides `quant-postgres` (with `db_gateway` and the
  `daily_performance` / `portfolio_snapshot` hypertables) and `quant-mongo`
  on the shared `quant-network`. Phase 2 onward is blocked until this stack
  is up.
- **[quant-csm-set](https://github.com/lumduan/csm-set)** — every Strategy
  Service must POST `StrategyPayload` JSON (Phase 2 schema) to
  `POST /api/v1/ingest/daily-report` with the `X-API-Key` header set to
  `INTERNAL_API_KEY`.

---

## Overall Exit Criteria

`docker compose up -d` from a fresh clone → everything is ready with no
extra configuration:

- `docker compose ps` shows `quant-api-gateway` and `quant-redis` as
  `(healthy)`
- `GET /health` returns `{"status": "ok"}` both from `localhost:8000` and
  from inside `quant-network` via the hostname `quant-api-gateway`
- `POST /api/v1/ingest/daily-report` accepts a `StrategyPayload` from
  `quant-csm-set` (with a valid `X-API-Key`) and writes a row to
  `db_gateway.daily_performance`
- `GET /api/v1/overall-performance` returns a complete
  `OverallPerformanceResponse` in < 200 ms on a cache hit
- Cache invalidation works end-to-end: a new ingestion clears the
  `overall_performance` key, and the next request returns fresh data
- Quality gate is green:
  `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest`
  with coverage ≥ 80% (target ≥ 90%)

---

## Current status

> Update this section as each phase completes.

- **Active phase:** Phase 3 — Strategy Ingestion & Data Storage
- **Completed phases:** Phase 1 — Project Bootstrap (2026-05-14), Phase 2 — Data Models & Schema Validation (2026-05-14)
- **Blocked by:** `quant-infra-db` must be running on `quant-network` before
  Phase 3 integration tests can run
- **Next:** Phase 3 — Strategy Ingestion & Data Storage

---

## Related notes

- [[quant-infra-db]] — Database infrastructure (PostgreSQL + TimescaleDB +
  MongoDB) shared by every service on `quant-network`
- [[quant-csm-set]] — Upstream Strategy Service that POSTs
  `StrategyPayload` JSON to the gateway
