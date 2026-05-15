# quant-api-gateway Documentation

quant-api-gateway is the Central Aggregator Service of the Quant Trading System. It ingests Daily Performance reports from Strategy Services, computes weighted return and combined drawdown across strategies, caches results in Redis, and exposes a versioned REST API.

```python
import httpx

# Health check
r = httpx.get("http://localhost:8000/health")
print(r.json())  # → {"status": "ok"}

# Fetch aggregated portfolio performance
async with httpx.AsyncClient() as client:
    resp = await client.get("http://localhost:8000/api/v1/overall-performance")
    perf = resp.json()
    print(f"Weighted return: {perf['weighted_daily_return']}")
    print(f"Active strategies: {perf['active_strategies']}")
```

---

## Recommended Learning Paths

**New to the project?** Follow this path:

1. [Getting Started](getting-started/quickstart.md) — clone, install, run locally
2. [Architecture Overview](architecture/system-overview.md) — how the layers fit together
3. [API Endpoints](reference/api/endpoints.md) — all 11 endpoints with request/response shapes
4. [Configuration](reference/config/settings.md) — every environment variable explained

**Implementing a new Strategy Service?** Jump to:

1. [Ingestion API](reference/api/ingest.md) — `POST /api/v1/ingest/daily-report` full specification
2. [Strategy Payload Schema](reference/schemas/strategy-payload.md) — exact JSON contract
3. [Authentication](reference/api/auth.md) — `X-API-Key` header format

**Building a Dashboard?** Start here:

1. [API Endpoints](reference/api/endpoints.md) — overview table with cache TTLs
2. [Response Schemas](reference/schemas/gateway.md) — `OverallPerformanceResponse`, etc.
3. [Caching Strategy](concepts/caching.md) — cache keys, TTLs, invalidation

---

## Getting Started 🚀

*Minimal steps to run the gateway.*

- [Quickstart](getting-started/quickstart.md) — clone, `uv sync`, `docker compose up -d`
- [Installation](getting-started/installation.md) — prerequisites, `.env` setup, Docker network
- [First Request](getting-started/first-request.md) — ingest a daily report, read performance

---

## Concepts 🧠

*Key terminology and design patterns used throughout the gateway.*

- [Caching](concepts/caching.md) — cache-aside pattern, key conventions, TTLs, invalidation
- [Data Flow](concepts/data-flow.md) — ingestion → Postgres → aggregation → Redis → API
- [Authentication](concepts/auth.md) — internal API key model, why read endpoints are open
- [UTC Timestamps](concepts/timestamps.md) — internal UTC enforcement, presentation boundaries
- [Error Handling](concepts/errors.md) — typed error hierarchy, graceful degradation

---

## Guides 📘

*Task-oriented tutorials with runnable examples.*

- [Running Locally](guides/local-development.md) — uvicorn, hot reload, quality gate
- [Docker Deployment](guides/docker-deployment.md) — build, compose, health checks, networking
- [Adding a Strategy](guides/adding-strategy.md) — edit `strategies.json`, ingest, verify
- [Debugging](guides/debugging.md) — structured JSON logs, request-ID tracing, container logs
- [Testing](guides/testing.md) — unit tests, integration tests, coverage requirements

---

## Reference 📚

*Complete API specifications — method signatures, parameters, return types, exceptions.*

### API Endpoints

- [Endpoints Overview](reference/api/endpoints.md) — all 11 endpoints in one table
- [Ingest Endpoint](reference/api/ingest.md) — `POST /api/v1/ingest/daily-report`
- [Performance Endpoints](reference/api/performance.md) — overall + strategy performance
- [Strategy Endpoints](reference/api/strategies.md) — list, detail, equity curve
- [Portfolio Endpoints](reference/api/portfolio.md) — snapshots, equity curve
- [Admin Endpoints](reference/api/admin.md) — cache flush

### Services (Business Logic)

- [Aggregator](reference/services/aggregator.md) — `calculate_weighted_return`, `merge_equity_curves`, `calculate_combined_drawdown`
- [Performance Service](reference/services/performance.md) — `compute_overall_performance`, `compute_strategy_performance`, `compute_strategy_performance_range`
- [Portfolio Service](reference/services/portfolio.md) — `query_latest_snapshot`, `query_snapshot_by_date`, `compute_portfolio_equity_curve`
- [Cache Service](reference/services/cache.md) — `get_cached`, `set_cached`, `invalidate_key`, `invalidate_pattern`
- [Cache Invalidator](reference/services/cache-invalidator.md) — `invalidate_overall_cache`, `invalidate_strategy_cache`, `flush_all`
- [Ingestion Service](reference/services/ingestion.md) — `persist_daily_report`, `_payload_to_row`
- [Snapshot Writer](reference/services/snapshot-writer.md) — `maybe_write_snapshot`, `_compute_aggregates`
- [Strategy Registry](reference/services/strategy-registry.md) — `load_registry`, `get_registry`

### Schemas

- [Strategy Payload (Input)](reference/schemas/strategy-payload.md) — `StrategyPayload`, `StrategyMetadata`, `PerformanceMetrics`, `CurrentExposure`, `EquityPoint`
- [Gateway Responses (Output)](reference/schemas/gateway.md) — `OverallPerformanceResponse`, `StrategyPerformanceResponse`, `PortfolioSnapshotResponse`
- [Strategy Registry](reference/schemas/registry.md) — `StrategyConfig`, `StrategyRegistry`

### Configuration & Infrastructure

- [Settings](reference/config/settings.md) — every `Settings` field with env var, type, default
- [Database Layer](reference/db/overview.md) — asyncpg pool, Redis client, MongoDB client
- [Application Lifecycle](reference/app/lifecycle.md) — startup, shutdown, middleware

### Logging & Observability

- [Structured Logging](reference/logging/structured-logging.md) — JSON formatter, field reference
- [Request-ID Middleware](reference/logging/request-id.md) — `X-Request-ID` header, ContextVar

---

## Architecture 🏗️

*High-level design documents.*

- [System Overview](architecture/system-overview.md) — all modules and their relationships
- [Data Flow](architecture/data-flow.md) — ingestion-to-Dashboard end-to-end
- [Module Boundaries](architecture/module-boundaries.md) — import rules, layer diagram

---

## Operations ⚙️

*Production and deployment reference.*

- [Docker Compose](operations/docker-compose.md) — service definitions, health checks, networking
- [Dockerfile](operations/dockerfile.md) — multi-stage build, non-root user, layers
- [Quality Gate](operations/quality-gate.md) — ruff, mypy, pytest, coverage requirements
- [Environment Variables](operations/environment.md) — every env var, where it's read, fallback

---

## Development 🛠️

*Contributor workflows.*

- [Feature Development](development/feature-workflow.md) — 8-step workflow from design to PR
- [Testing Strategy](development/testing-strategy.md) — unit vs integration, mocking patterns
- [Phase Plans](development/phase-plans.md) — index of all phase implementation plans

---

## Support 📖

- [FAQ](faq.md)
- [ROADMAP](../docs/plans/ROADMAP.md)
- [CHANGELOG](../CHANGELOG.md)
- [Contributing](../CONTRIBUTING.md)
- [Security](../SECURITY.md)

---

## Need Help?

- [Open a GitHub issue](https://github.com/lumduan/quant-api-gateway/issues)
- Check structured logs: `docker compose logs api-gateway | jq .`
- Swagger UI: `http://localhost:8000/docs`
