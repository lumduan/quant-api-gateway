# quant-api-gateway

[![CI](https://github.com/lumduan/quant-api-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/lumduan/quant-api-gateway/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-green.svg)](https://fastapi.tiangolo.com/)
[![Coverage](https://img.shields.io/badge/coverage-94%25-brightgreen.svg)](https://github.com/lumduan/quant-api-gateway)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)

Central Aggregator Service for the Quant Trading System.

Ingest Daily Performance reports from Strategy Services, compute weighted return and combined drawdown across strategies, cache results in Redis, and serve a versioned REST API — all on the shared Docker network `quant-network`.

## Features

- **Strategy ingestion** — accept `StrategyPayload` JSON from any strategy service (currently `quant-csm-set`) via authenticated `POST /api/v1/ingest/daily-report`
- **Weighted aggregation** — capital-weighted daily return, combined max drawdown, and merged equity curves across all active strategies
- **Redis cache-aside** — configurable TTLs per cache key, SCAN-based invalidation, graceful degradation on cache failure
- **Portfolio snapshots** — automatic daily snapshot writing when all strategies have reported
- **11 REST endpoints** — overall performance, strategy detail, date-range history, equity curves, portfolio snapshots, admin cache flush
- **Pydantic v2 at boundaries** — every payload entering or leaving the gateway is validated; Decimal precision for all financial fields
- **Structured JSON logging** — single-line JSON records with UTC timestamps, log levels, and request-ID tracing
- **Non-root Docker container** — multi-stage build, `HEALTHCHECK`, health-checked Redis sidecar
- **Quality gate ≥90% coverage** — ruff, mypy strict, pytest with branch coverage, integration test suite

## Quick Example

```python
import httpx
import asyncio

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

## Installation

```bash
git clone https://github.com/lumduan/quant-api-gateway.git
cd quant-api-gateway

uv sync --all-groups
uv run pre-commit install

cp .env.example .env   # fill in real values
```

## Running locally

```bash
uv run uvicorn src.main:app --reload
# → http://localhost:8000/health → {"status":"ok"}
# → http://localhost:8000/docs
```

## Running on `quant-network` via Docker Compose

```bash
# Ensure quant-network exists (created by quant-infra-db)
docker network create quant-network 2>/dev/null || true

docker compose up -d
docker compose ps          # quant-api-gateway + quant-redis → (healthy)

curl -s localhost:8000/health
# → {"status":"ok"}

# From inside quant-network:
docker run --rm --network quant-network curlimages/curl \
  curl -s http://quant-api-gateway:8000/health
# → {"status":"ok"}

docker compose down
```

Host port is configurable via `API_GATEWAY_HOST_PORT` in `.env` (default `8000`). Set to `8080` if another service already binds `:8000`.

## Ingest a daily report

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
| GET | `/api/v1/portfolio/snapshot/{date}` | — | Snapshot for specific date | 3600 s |
| GET | `/api/v1/portfolio/equity-curve` | — | Merged portfolio equity curve | — |
| POST | `/api/v1/admin/cache/flush` | `X-API-Key` | Flush Redis cache | — |

## Documentation

Full documentation index → [docs/index.md](docs/index.md)

### Getting Started

- [Quickstart](docs/getting-started/quickstart.md) — clone, install, run, first request in 5 minutes
- [Installation](docs/getting-started/installation.md) — prerequisites, `.env`, Docker network setup
- [First Request](docs/getting-started/first-request.md) — ingest a report and read it back

### Concepts

- [Caching](docs/concepts/caching.md) — cache-aside pattern, key conventions, TTLs, invalidation
- [Data Flow](docs/concepts/data-flow.md) — ingestion → Postgres → aggregation → Redis → API
- [Authentication](docs/concepts/auth.md) — `X-API-Key` model, why read endpoints are open
- [Error Handling](docs/concepts/errors.md) — typed error hierarchy, graceful degradation

### Guides

- [Local Development](docs/guides/local-development.md) — uvicorn, hot reload, quality gate workflow
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

- [System Overview](docs/architecture/system-overview.md) — all modules, their relationships, data flow
- [Data Flow](docs/architecture/data-flow.md) — ingestion-to-Dashboard end-to-end

### Operations

- [Docker Compose](docs/operations/docker-compose.md) — service definitions, health checks, networking
- [Quality Gate](docs/operations/quality-gate.md) — ruff, mypy, pytest, coverage requirements
- [Environment Variables](docs/operations/environment.md) — every env var, where it's read, fallback

### Development

- [Feature Workflow](docs/development/feature-workflow.md) — 8-step workflow from design to PR
- [Phase Plans Index](docs/development/phase-plans.md) — all phase implementation plans

### Support

- [FAQ](docs/faq.md)
- [ROADMAP](docs/plans/ROADMAP.md)
- [CHANGELOG](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

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

## Security scanning

```bash
uv run bandit -r src
uv run pip-audit
```

Both run automatically on a weekly CI schedule (`.github/workflows/security.yml`).

## Working with AI agents (`.claude/`)

This repository is set up for AI coding agents (Claude Code, Cursor, etc.):

| File | Purpose |
|---|---|
| **`docs/PROJECT.md`** | **Complete project reference.** Every module, function, schema, endpoint, and design decision. LLM-friendly. |
| **`docs/index.md`** | Documentation hub — learning paths, concepts, guides, reference |
| `.claude/knowledge/project-skill.md` | Hard rules and quality gates |
| `.claude/knowledge/architecture.md` | Module boundaries and data flow |
| `.claude/knowledge/coding-standards.md` | Naming, typing, docstrings, async |
| `.claude/knowledge/commands.md` | Full command reference |
| `.claude/knowledge/stack-decisions.md` | Why each tool was chosen |
| `.claude/playbooks/feature-development.md` | 8-step feature workflow |

## Documentation index

| Document | Covers |
|---|---|
| [`docs/index.md`](docs/index.md) | Doc hub with learning paths and full section map |
| [`docs/PROJECT.md`](docs/PROJECT.md) | Complete reference: architecture, schemas, services, endpoints, config, Docker, quality gate |
| [`docs/getting-started/quickstart.md`](docs/getting-started/quickstart.md) | 5-minute setup and first request |
| [`docs/reference/api/endpoints.md`](docs/reference/api/endpoints.md) | All 11 endpoints with parameters and response models |
| [`docs/reference/services/aggregator.md`](docs/reference/services/aggregator.md) | Pure aggregation functions with formulas and examples |
| [`docs/reference/config/settings.md`](docs/reference/config/settings.md) | Every Settings field with type, default, and env var |
| [`docs/plans/ROADMAP.md`](docs/plans/ROADMAP.md) | Phased build-out with acceptance criteria and status |
| [`docs/plans/phase_7_operations_quality_gate/`](docs/plans/phase_7_operations_quality_gate/) | Phase 7 plan and post-implementation notes |
| [`docs/plans/phase_6_rest_api_endpoints/`](docs/plans/phase_6_rest_api_endpoints/) | Phase 6 REST API plan with design decisions |
| [`docs/plans/phase_5_redis_caching/`](docs/plans/phase_5_redis_caching/) | Phase 5 cache-aside architecture |
| [`docs/plans/phase_4_aggregation_engine/`](docs/plans/phase_4_aggregation_engine/) | Phase 4 aggregation formulas and test fixtures |
| [`docs/plans/phase_3_strategy_ingestion/`](docs/plans/phase_3_strategy_ingestion/) | Phase 3 ingestion flow and snapshot writer |
| [`docs/plans/phase_2_data_models/`](docs/plans/phase_2_data_models/) | Phase 2 Pydantic V2 schemas |
| [`docs/plans/phase_1_bootstrap/`](docs/plans/phase_1_bootstrap/) | Phase 1 project skeleton |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). All commits use
[Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).

## License

MIT — see [`LICENSE`](LICENSE).
