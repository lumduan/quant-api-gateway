# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (feature-execution-engine — Phase 2: execution engine proxy)

- **`/api/v2/engines/execution/*`** — thin reverse proxy to the standalone
  `quant-execution-engine` (host `:8400`): `GET /health`, `GET /capabilities`,
  `POST /orders`, `GET`/`DELETE /orders/{client_order_id}`. Mirrors the market-data
  proxy, extended to forward the method + raw request body. Engine 4xx typed
  envelopes pass through verbatim; transport failures map to 502/503/504; the
  engine's `/admin/*` kill-switch surface is deliberately not proxied. **The gateway
  holds no broker credential (D2).** New settings `execution_engine_service_url` /
  `execution_engine_timeout_seconds`; `execution` added to the static engine catalog
  (live `engine_registry` row in quant-infra-db#13); 13-case proxy test suite.

### Added

- **Phase 1 — Project Bootstrap.** Replaces the `python-template` skeleton with the `quant-api-gateway` FastAPI service:
  - `src/main.py` — FastAPI app with an async `lifespan`, root-level `GET /health → {"status":"ok"}`, and a v1 router mounted under `/api/v1` as the future home of ingest/performance/strategies/portfolio sub-routers.
  - `src/config.py` — `Settings` model (Pydantic Settings) covering `POSTGRES_DSN`, `MONGO_URI`, `REDIS_URL`, `CSM_SET_SERVICE_URL`, `INTERNAL_API_KEY`, `LOG_LEVEL`; cached accessor `get_settings()`.
  - `src/api/v1/router.py` — empty `APIRouter` stub for the v1 mount point.
  - `docker-compose.yml` — `quant-api-gateway` + `quant-redis` services on the external Docker network `quant-network`, with healthchecks for both.
  - `Dockerfile` rewritten for `python:3.12-slim`, uv-native multi-stage build, `curl` installed in the runtime stage for the Compose healthcheck, `uvicorn` CMD on port 8000.
  - `.env.example` — gateway-specific environment template.
  - Tests: 12 tests (`tests/test_main.py`, `tests/test_config.py`, `tests/api/v1/test_router.py`) covering app metadata, `/health` integration, OpenAPI generation, Settings validation, and the `lifespan` startup/shutdown branches. Coverage: **100%**.
  - `docs/plans/phase_1_bootstrap/phase_1_bootstrap.md` — Phase 1 implementation plan.
- Initial roadmap document for the `quant-api-gateway` service at `docs/plans/ROADMAP.md` — covers Phases 1–7 (Bootstrap, Data Models, Ingestion, Aggregation, Caching, REST API, Operations) with task checklists, exit criteria, code snippets, dependency map, and external project dependencies.

### Changed

- Project renamed in `pyproject.toml` from `python-template` to `quant-api-gateway`; runtime dependencies added (`fastapi`, `uvicorn[standard]`, `asyncpg`, `motor`, `redis[asyncio]`, `pydantic`, `pydantic-settings`, `httpx`).
- `README.md` rewritten for the new project (overview, endpoints, prerequisites, local run, Docker Compose run, quality gate, `.claude/` agent map).

### Security

- Bumped transitive dependency `urllib3` from 2.6.3 to 2.7.0 to address CVE-2026-44431 and CVE-2026-44432 (`uv run pip-audit` now reports no known vulnerabilities).
- Initial template scaffold: `src/`, `tests/`, `docs/`, `.claude/`, `.github/`.
- `pyproject.toml` with `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `pytest-cov`, `bandit`, `pip-audit`.
- Multi-stage `Dockerfile` (uv-native, Python 3.11-slim).
- CI workflow (lint, format check, type check, test with coverage) on Python 3.11 and 3.12.
- Docker publish workflow targeting GHCR.
- Weekly security scan workflow (`bandit` + `pip-audit`).
- AI-agent enablement: `.claude/knowledge/project-skill.md`, `.claude/playbooks/feature-development.md`, `.claude/prompts/Prompt-Engineer.prompt.md`.
- Issue templates (bug, feature), PR template, `FUNDING.yml`.

[Unreleased]: https://github.com/OWNER/REPO/compare/HEAD...HEAD
