# quant-api-gateway

> Central Aggregator Service for the Quant Trading System.

[![CI](https://github.com/lumduan/quant-api-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/lumduan/quant-api-gateway/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`quant-api-gateway` ingests Daily Performance reports from every Strategy
Service (currently `quant-csm-set`), computes weighted return and combined
drawdown across strategies, caches the result in Redis, and exposes a
versioned REST API that the React Dashboard and any other client can read
from.

The service runs as a FastAPI container on the shared Docker network
`quant-network`, alongside the [`quant-infra-db`](https://github.com/lumduan/quant-infra-db)
stack (`quant-postgres`, `quant-mongo`, `quant-redis`).

Current status: **Phase 7 — Operations & Quality Gate complete.** All 11
endpoints are live, the Docker stack runs with non-root user and health checks,
structured JSON logging is in place, and coverage is ≥90%. See
[`docs/plans/ROADMAP.md`](docs/plans/ROADMAP.md) for the full build-out.

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

## Prerequisites

- Python 3.11 or 3.12
- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker + Docker Compose v2

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

The Compose stack joins the **external** Docker network `quant-network`. If
the network does not exist yet (i.e. `quant-infra-db` has not been brought
up first), create it once:

```bash
docker network create quant-network 2>/dev/null || true
```

Then:

```bash
docker compose up -d
docker compose ps          # quant-api-gateway and quant-redis → (healthy)
curl -s localhost:8000/health
docker compose down
```

The gateway listens on container port `8000` and publishes to **host port
`${API_GATEWAY_HOST_PORT}`** (default `8000`). If another service on the
host already binds `:8000` — e.g. the upstream `quant-csm-set` Strategy
Service — set a different value in your `.env`:

```env
API_GATEWAY_HOST_PORT=8080
```

…and curl `localhost:8080/health` instead. The container-side port and
all in-network communication are unaffected.

Inside `quant-network`, the gateway and redis are reachable by hostname
(`quant-api-gateway:8000`, `quant-redis:6379`).

## Quality gate

All four checks must pass before any commit:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -v --cov=src --cov-report=term-missing
```

Or combined:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest
```

Coverage gate: **≥90% global** (enforced by `pyproject.toml` and CI).

## Security scanning

```bash
uv run bandit -r src
uv run pip-audit
```

Both run automatically on a weekly CI schedule
(`.github/workflows/security.yml`).

## Working with AI agents (`.claude/`)

This repository is set up for AI coding agents (Claude Code, Cursor, etc.):

| File | Purpose |
|---|---|
| **`docs/PROJECT.md`** | **Complete project reference.** Every module, function, schema, endpoint, and design decision. LLM-friendly. |
| `.claude/knowledge/project-skill.md` | Hard rules and quality gates. |
| `.claude/knowledge/architecture.md` | Module boundaries and data flow. |
| `.claude/knowledge/coding-standards.md` | Naming, typing, docstrings, async. |
| `.claude/knowledge/commands.md` | Full command reference. |
| `.claude/knowledge/stack-decisions.md` | Why each tool was chosen. |
| `.claude/playbooks/feature-development.md` | 8-step feature workflow. |
| `docs/plans/ROADMAP.md` | Phased build-out (currently the source of truth). |
| `docs/plans/phase_*/` | Per-phase implementation plans with post-implementation notes. |

## Documentation index

| Document | Covers |
|---|---|
| [`docs/PROJECT.md`](docs/PROJECT.md) | Architecture, schemas, services, endpoints, config, database, Docker, quality gate, caching, data flow — the complete reference |
| [`docs/plans/ROADMAP.md`](docs/plans/ROADMAP.md) | Phased build-out with per-feature acceptance criteria, exit criteria, and current status |
| [`docs/plans/phase_7_operations_quality_gate/`](docs/plans/phase_7_operations_quality_gate/) | Phase 7 implementation plan and post-implementation notes |
| [`docs/plans/phase_6_rest_api_endpoints/`](docs/plans/phase_6_rest_api_endpoints/) | Phase 6 REST API build plan with design decisions and hand-off notes |
| [`docs/plans/phase_5_redis_caching/`](docs/plans/phase_5_redis_caching/) | Phase 5 cache-aside architecture and invalidation design |
| [`docs/plans/phase_4_aggregation_engine/`](docs/plans/phase_4_aggregation_engine/) | Phase 4 aggregation formulas and test fixtures |
| [`docs/plans/phase_3_strategy_ingestion/`](docs/plans/phase_3_strategy_ingestion/) | Phase 3 ingestion flow and snapshot writer design |
| [`docs/plans/phase_2_data_models/`](docs/plans/phase_2_data_models/) | Phase 2 Pydantic V2 schemas and database layer |
| [`docs/plans/phase_1_bootstrap/`](docs/plans/phase_1_bootstrap/) | Phase 1 project skeleton and Docker Compose setup |
| `docs/plans/ROADMAP.md` | Phased build-out (currently the source of truth). |
| `docs/plans/phase_*/` | Per-phase implementation plans. |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). All commits use
[Conventional Commits](https://www.conventionalcommits.org/) (`feat:`,
`fix:`, `docs:`, `chore:`, `refactor:`).

## Security

Report vulnerabilities privately to **bad.sonsuk@gmail.com**. See
[`SECURITY.md`](SECURITY.md).

## License

MIT — see [`LICENSE`](LICENSE).
