# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

This repo is the **`quant-api-gateway`** — the Central Aggregator Service of the Quant Trading System. It ingests Daily Performance reports from Strategy Services (currently `quant-csm-set`), computes weighted return and combined drawdown across strategies, caches results in Redis, and exposes a versioned REST API for the React Dashboard and other clients.

The runtime is a FastAPI container on the shared Docker network `quant-network`, alongside the `quant-infra-db` stack (`quant-postgres`, `quant-mongo`, `quant-redis`). A Redis sidecar is bundled in `docker-compose.yml`.

The full set of endpoints, request/response examples, and the `X-API-Key` flow live in `README.md` and `docs/PROJECT.md`; read those before adding or changing an endpoint. Phase plans for in-flight work live under `docs/plans/phase_*/` and the canonical roadmap is `docs/plans/ROADMAP.md`.

## Commands

Every Python invocation must be prefixed with `uv run`. Never `python`, `pip`, `poetry`, or `conda` directly.

**Quality gate (run all four before any commit):**
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest
```

**Individual:**
- `uv run pytest -v` — unit tests; the default pytest `addopts` includes `-m "not integration"` so integration tests are skipped here
- `uv run pytest -m integration -v` — run the integration suite (requires the `quant-network` stack)
- `uv run pytest tests/<path>::<test_name> -v` — single test
- `uv run pytest --cov=src --cov-report=term-missing` — coverage report (gate is **≥90%**, enforced via `--cov-fail-under=90` in `pyproject.toml`)
- `uv run mypy src tests` — strict type check (`mypy` is configured `strict = true`)
- `uv run ruff check .` / `uv run ruff format .` — lint / auto-format
- `uv run pre-commit run --all-files` — run all hooks locally
- `uv run uvicorn src.main:app --reload` — run the API locally (note: `src/main.py` has no `__main__` block, so `python -m src.main` will not work)
- `uv run bandit -r src` / `uv run pip-audit` — security scans

**Dependencies:**
- `uv sync --all-groups` — install (`uv.lock` is committed and authoritative)
- `uv add <pkg>` / `uv add --dev <pkg>` — add deps
- `uv lock --upgrade-package <pkg> && uv sync` — bump one package

**Docker / Compose:**
- `docker network create quant-network 2>/dev/null || true` — ensure the shared network exists once
- `docker compose up -d` — start the gateway + Redis sidecar (joins `quant-network` as external)
- `docker build -t quant-api-gateway:dev .` — local image build
- Host port is `${API_GATEWAY_HOST_PORT:-8000}`; override in `.env` if `:8000` is taken

A more exhaustive command catalogue lives in `.claude/knowledge/commands.md`.

## Architecture

Layout under `src/`:

```
src/
├── main.py              # FastAPI app, async lifespan, RequestIDMiddleware, /health
├── config.py            # Pydantic-Settings; lazy get_settings() with lru_cache
├── logging_config.py    # Structured JSON logging + request_id_var ContextVar
├── schemas/             # Pydantic v2 boundary models
│   ├── strategy.py      #   StrategyPayload (ingest input)
│   ├── gateway.py       #   API response models
│   ├── registry.py      #   strategies.json schema
│   └── errors.py
├── db/                  # async clients (asyncpg, motor, redis.asyncio)
│   ├── postgres.py      #   get_pool() / close_pool() — eager init in lifespan
│   ├── redis_client.py  #   get_redis() / close_redis() — eager init in lifespan
│   └── mongo.py         #   lazy until a later phase needs it
├── services/            # business logic; no FastAPI imports here
│   ├── strategy_registry.py  # loads strategies.json at startup
│   ├── ingestion.py          # validates + persists incoming reports
│   ├── aggregator.py         # weighted_return, merge_equity_curves, combined_drawdown
│   ├── performance.py
│   ├── portfolio.py
│   ├── snapshot_writer.py    # writes a daily snapshot once every active strategy has reported
│   ├── cache.py              # cache-aside helpers
│   ├── cache_invalidator.py  # SCAN-based invalidation
│   └── errors.py
└── api/v1/              # routers mounted under /api/v1 by src/api/v1/router.py
    ├── ingest.py        #   POST /ingest/daily-report  (X-API-Key required)
    ├── strategies.py    #   GET  /strategies, /strategies/{id}, /strategies/{id}/equity-curve
    ├── performance.py   #   GET  /overall-performance, /strategies/{id}/performance
    ├── portfolio.py     #   GET  /portfolio/snapshot[/{date}], /portfolio/equity-curve
    ├── admin.py         #   POST /admin/cache/flush         (X-API-Key required)
    └── dependencies.py  #   shared FastAPI dependencies (auth, etc.)
```

Data flow is layered and one-way — lower layers must not import from higher ones:

```
schemas → db → services → api → main
```

`services/` must never import from `api/` or `main`. Each subpackage that needs its own exception hierarchy owns an `errors.py` rooted at a single base exception (currently `src/schemas/errors.py` and `src/services/errors.py`).

**Key runtime behaviors a new agent needs to know:**

- **Strategy registry** — `strategies.json` at the repo root lists active strategies and their capital weights; loaded into memory at FastAPI startup via the lifespan, path overridable through `Settings.strategy_registry_path`. Adding a strategy is a JSON edit, not a code change.
- **Ingestion auth** — `POST /api/v1/ingest/daily-report` and `POST /api/v1/admin/cache/flush` require the `X-API-Key` header matching `Settings.internal_api_key`. All read endpoints are open.
- **Idempotent ingest** — Postgres uses `INSERT … ON CONFLICT` on `(strategy_id, last_updated)`, so re-posting the same report is safe.
- **Auto-snapshot** — `services/snapshot_writer.py` writes a `portfolio_snapshot` row and invalidates cache keys *only when every active strategy has reported for the day*. Don't trigger snapshots from elsewhere.
- **Cache-aside with configurable TTLs** — defaults are 300 s for overall/strategy performance and 3600 s for portfolio snapshots; all three are env-configurable via `Settings.*_ttl_seconds`. Cache failures degrade gracefully (compute fresh, log warning).
- **Request tracing** — `RequestIDMiddleware` in `main.py` generates a UUID per request, sets it on `request.state.request_id` and the `request_id_var` ContextVar, and echoes it back as `X-Request-ID`. The JSON logger includes it automatically.
- **Decimal for money** — every financial field on the Pydantic boundary models uses `Decimal`, not `float`. Don't convert to `float` in aggregation paths.
- **UTC everywhere** — timestamps are UTC internally; localize only at presentation boundaries.

## Hard rules (from `.claude/knowledge/project-skill.md`)

1. **`uv run` everywhere** — no bare `python`/`pip`.
2. **Async-first I/O at boundaries.** All HTTP via `httpx.AsyncClient` with explicit `timeout=`. `requests` is forbidden in library code (it blocks the event loop). Sync internal compute is fine.
3. **Pydantic v2 at boundaries.** Data crossing module/process boundaries is a Pydantic model, never a raw dict.
4. **Full type annotations** on every public function (args + return). No bare `Any` — if unavoidable, justify in a comment. Prefer `Sequence`/`Mapping`/`Iterable` for params; concrete `list`/`dict` for returns.
5. **Logging, not `print`.** `logger = logging.getLogger(__name__)` at module top. `%`-formatting for deferred interpolation (`logger.info("processed %d items", n)`). Never log secrets, tokens, or full request bodies.
6. **Config via `pydantic-settings`** reading env vars (or `.env` for local dev). No hard-coded paths — read from `Settings`.
7. **Conventional Commits** (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).

File-size target: ≤500 lines per `.py` file; split into a package when exceeded. Imports are ruff-isort sorted (stdlib → third-party → local); no relative imports beyond one level; no wildcard imports.

## Tests

- `tests/` mirrors `src/` — one test file per source file. Integration tests live under `tests/integration/`.
- `pytest-asyncio` is configured with `asyncio_mode = "auto"`, so `async def test_…` works without per-test markers.
- Default `addopts` is `--cov=src --cov-report=term-missing --cov-fail-under=90 -m "not integration"`. That means `uv run pytest` enforces the 90% coverage gate locally *and* hides integration tests by default — opt in with `-m integration`.
- No network in unit tests. `tests/strategies.fixture.json` is the canonical fixture for registry tests.

## Agent context — where to look first

The repo ships a `.claude/` directory used by AI agents. When a topic comes up, prefer reading the canonical doc rather than guessing:

- `.claude/knowledge/project-skill.md` — master hard rules (start here)
- `.claude/knowledge/architecture.md` — module boundaries and data flow
- `.claude/knowledge/coding-standards.md` — naming, typing, docstrings, async, error handling
- `.claude/knowledge/commands.md` — full command reference
- `.claude/knowledge/stack-decisions.md` — why each tool was chosen (and what's deliberately *not* used)
- `.claude/playbooks/feature-development.md` — 8-step workflow: read → design → test-first → implement → quality gate → document → commit → verify-in-Docker
- `.claude/playbooks/bugfix-workflow.md`, `code-review.md`, `dependency-upgrade.md`, `release-checklist.md` — task-specific playbooks
- `.claude/agents/` — role-scoped agent prompts (python-architect, test-engineer, security-reviewer, etc.)
- `docs/PROJECT.md` — exhaustive single-file reference: every module, schema, endpoint, design decision
- `docs/plans/ROADMAP.md` and `docs/plans/phase_*/` — phased build-out, source of truth for what's next

## What this project deliberately doesn't use

`requests` (sync — use `httpx`), `poetry`/`pip-tools`/`conda` (replaced by `uv`), `flake8`/`isort`/`black` (replaced by `ruff`), `float` for money (use `Decimal`). Don't reintroduce these.
