# quant-api-gateway

[![CI](https://github.com/lumduan/quant-api-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/lumduan/quant-api-gateway/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-green.svg)](https://fastapi.tiangolo.com/)
[![Coverage](https://img.shields.io/badge/coverage-94%25-brightgreen.svg)](https://github.com/lumduan/quant-api-gateway)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)

Central Aggregator Service for the Quant Trading System — ingest daily performance from strategy services, compute weighted return and combined drawdown, cache in Redis, and serve a versioned REST API on `quant-network`.

## Features

- **Strategy ingestion** — accept `StrategyPayload` JSON from any strategy service (currently `quant-csm-set`) via authenticated `POST /api/v1/ingest/daily-report`
- **Weighted aggregation** — capital-weighted daily return, combined max drawdown, and merged equity curves across all active strategies
- **Redis cache-aside** — configurable TTLs per cache key, SCAN-based invalidation, graceful degradation on cache failure
- **Portfolio snapshots** — automatic daily snapshot writing when all strategies have reported for the day
- **11 REST endpoints** — overall performance, strategy detail, date-range history, equity curves, portfolio snapshots, admin cache flush
- **Pydantic v2 at boundaries** — every payload entering or leaving the gateway is validated; `Decimal` precision for all financial fields
- **Structured JSON logging** — single-line JSON records with UTC timestamps, log levels, and request-ID tracing
- **Non-root Docker container** — multi-stage build, `HEALTHCHECK`, health-checked Redis sidecar
- **Quality gate ≥90% coverage** — ruff, mypy strict, pytest with branch coverage, integration test suite

## Quick Example

```python
import asyncio
import httpx

async def main() -> None:
    async with httpx.AsyncClient() as client:
        # Health check
        r = await client.get("http://localhost:8000/health")
        print(r.json())  # → {"status": "ok"}

        # Overall portfolio performance
        resp = await client.get("http://localhost:8000/api/v1/overall-performance")
        perf = resp.json()
        print(f"Total value: {perf['total_portfolio_value']}")
        print(f"Weighted return: {perf['weighted_daily_return']}")
        print(f"Active strategies: {perf['active_strategies']}")

asyncio.run(main())
```

See more working examples in the [quickstart guide](docs/getting-started/quickstart.md).

## Installation

Available on GitHub: <https://github.com/lumduan/quant-api-gateway>

```bash
git clone https://github.com/lumduan/quant-api-gateway.git
cd quant-api-gateway

uv sync --all-groups           # recommended
uv run pre-commit install

cp .env.example .env           # fill in real values
```

## Ingestion

Strategy services POST daily performance reports with the `X-API-Key` header. The gateway validates, persists, and triggers snapshot computation.

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest/daily-report \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${INTERNAL_API_KEY}" \
  -d '{
    "strategy_metadata": {
        "id": "csm-set-01",
        "type": "equity-long",
        "last_updated": "2026-05-15T14:00:00Z"
    },
    "performance_metrics": {
        "daily_pnl": "15000.50",
        "equity_curve": [{"date": "2026-05-15", "value": "1050000.00"}],
        "max_drawdown": "-0.063",
        "sharpe_ratio": "1.85"
    },
    "current_exposure": {
        "total_value": "1050000.00",
        "cash_balance": "50000.00",
        "positions_count": 5
    }
}'
# → {"status":"accepted","strategy_id":"csm-set-01","time":"2026-05-15T14:00:00+00:00"}
```

Resending the same report is idempotent — `INSERT ON CONFLICT` overwrites on matching `strategy_id` + `last_updated`. Every successful ingest checks whether all active strategies have reported for the day; if so, a portfolio snapshot is written and cached results are invalidated.

## API Endpoints

| Method | Path | Auth | Description | Cache TTL |
|--------|------|------|-------------|-----------|
| GET | `/health` | — | Health check | — |
| POST | `/api/v1/ingest/daily-report` | `X-API-Key` | Ingest daily performance | — |
| GET | `/api/v1/overall-performance` | — | Aggregated portfolio performance | 300 s |
| GET | `/api/v1/strategies` | — | List all active strategies | — |
| GET | `/api/v1/strategies/{id}` | — | Single strategy detail | — |
| GET | `/api/v1/strategies/{id}/performance` | — | Latest or date-range performance | 300 s (latest only) |
| GET | `/api/v1/strategies/{id}/equity-curve` | — | Full equity curve | — |
| GET | `/api/v1/portfolio/snapshot` | — | Latest portfolio snapshot | 3600 s |
| GET | `/api/v1/portfolio/snapshot/{date}` | — | Snapshot for specific date (YYYY-MM-DD) | 3600 s |
| GET | `/api/v1/portfolio/equity-curve` | — | Merged portfolio equity curve | — |
| POST | `/api/v1/admin/cache/flush` | `X-API-Key` | Flush all gateway cache keys | — |

Date-range query on strategy performance:

| Parameter | Type | Description |
|-----------|------|-------------|
| `?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD` | Both `date` | Returns `list[StrategyPerformanceResponse]` (uncached) |
| *(no params)* | — | Returns latest `StrategyPerformanceResponse` (cached) |
| *(only one param)* | — | Returns `422` with actionable message |

Portfolio equity curve normalization:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `?normalize=true` | `bool` | `true` | Normalize each curve to base 100 before merging |
| `?normalize=false` | `bool` | — | Return raw cumulative values |

## Running on `quant-network`

```bash
# Ensure quant-network exists (created once by quant-infra-db)
docker network create quant-network 2>/dev/null || true

docker compose up -d
docker compose ps          # quant-api-gateway + quant-redis → (healthy)

curl -s localhost:8000/health
# → {"status":"ok"}

# From inside quant-network:
docker run --rm --network quant-network curlimages/curl \
  curl -s http://quant-api-gateway:8000/health
# → {"status":"ok"}
```

Host port is configurable via `API_GATEWAY_HOST_PORT` in `.env` (default `8000`). Set to `8080` if another service already binds `:8000`.

Running locally without Docker:

```bash
uv run uvicorn src.main:app --reload
# → http://localhost:8000/health → {"status":"ok"}
# → http://localhost:8000/docs
```

## Documentation

Full documentation index → [docs/index.md](docs/index.md)

### Getting Started

- [Quickstart](docs/getting-started/quickstart.md) — clone, install, run, first request in 5 minutes
- [Installation](docs/getting-started/installation.md) — prerequisites, `.env` setup, Docker network
- [First Request](docs/getting-started/first-request.md) — ingest a report and read back performance

### Concepts

- [Caching](docs/concepts/caching.md) — cache-aside pattern, key conventions, TTLs, invalidation
- [Data Flow](docs/concepts/data-flow.md) — ingestion → Postgres → aggregation → Redis → API
- [Authentication](docs/concepts/auth.md) — `X-API-Key` model, why read endpoints are open
- [Error Handling](docs/concepts/errors.md) — typed error hierarchy, graceful degradation

### Guides

- [Local Development](docs/guides/local-development.md) — uvicorn, hot reload, quality gate
- [Docker Deployment](docs/guides/docker-deployment.md) — build, compose, health checks, networking
- [Adding a Strategy](docs/guides/adding-strategy.md) — edit `strategies.json`, ingest, verify
- [Testing](docs/guides/testing.md) — unit tests, integration tests, coverage requirements

### Reference

- [API Endpoints](docs/reference/api/endpoints.md) — all 11 endpoints with parameters and responses
- [Aggregator](docs/reference/services/aggregator.md) — `calculate_weighted_return`, `merge_equity_curves`, `calculate_combined_drawdown`
- [Settings](docs/reference/config/settings.md) — every `Settings` field with env var, type, default
- [Strategy Payload Schema](docs/reference/schemas/strategy-payload.md) — input JSON contract
- [Gateway Response Schemas](docs/reference/schemas/gateway.md) — output model reference

### Architecture

- [System Overview](docs/architecture/system-overview.md) — all modules, data flow, runtime topology

### Operations

- [Docker Compose](docs/operations/docker-compose.md) — service definitions, health checks
- [Quality Gate](docs/operations/quality-gate.md) — ruff, mypy, pytest, coverage thresholds
- [Environment Variables](docs/operations/environment.md) — every env var, where it's read, fallback

### Development

- [Feature Workflow](docs/development/feature-workflow.md) — 8-step workflow from design to PR
- [Phase Plans](docs/development/phase-plans.md) — index of all phase implementation plans

### Support

- [FAQ](docs/faq.md)
- [ROADMAP](docs/plans/ROADMAP.md)
- [CHANGELOG](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

### AI Agents

This repository is set up for AI coding agents (Claude Code, Cursor, etc.):

| File | Purpose |
|---|---|
| **`docs/PROJECT.md`** | **Complete project reference.** Every module, function, schema, endpoint, and design decision. |
| **`docs/index.md`** | Documentation hub with learning paths and section map |
| `.claude/knowledge/project-skill.md` | Hard rules and quality gates |
| `.claude/playbooks/feature-development.md` | 8-step feature workflow |

## Why quant-api-gateway

The Quant Trading System needs a single source of truth for portfolio-wide performance. Without a central aggregator, each strategy service would need to know about every other strategy to compute weighted returns — tightly coupling services that should be independent.

The gateway provides:
- A clean REST API for dashboards and third-party clients
- Redis caching so reads are fast (< 200 ms on cache hit)
- Pydantic validation at every boundary so malformed data never reaches downstream code
- A strategy registry that makes adding new strategies a JSON edit, not a code change

## Quality gate

All four checks must pass before any commit:

```bash
uv run ruff check .        # Zero findings required
uv run ruff format --check .   # No formatting drift
uv run mypy src tests      # Strict mode, zero errors
uv run pytest -v --cov=src --cov-report=term-missing --cov-fail-under=90
```

Integration tests (require running infrastructure):

```bash
uv run pytest -m integration -v
```

Security scanning:

```bash
uv run bandit -r src
uv run pip-audit
```

Both run automatically on a weekly CI schedule (`.github/workflows/security.yml`).

## Stability

quant-api-gateway is under active development. The public API (`/api/v1/`) is expected to remain stable within minor versions. Breaking changes will follow semantic versioning. See [ROADMAP.md](docs/plans/ROADMAP.md) for planned features.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development environment setup, quality gate commands, and pull request process. All commits use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).

## License

MIT — see [`LICENSE`](LICENSE)
