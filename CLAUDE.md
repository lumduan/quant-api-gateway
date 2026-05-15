# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

This repo is the **`quant-api-gateway`** — the Central Aggregator Service of the Quant Trading System. It ingests Daily Performance reports from Strategy Services (currently `quant-csm-set`), computes weighted return and combined drawdown across strategies, caches results in Redis, and exposes a versioned REST API for the React Dashboard and other clients.

The intended runtime is a FastAPI container on the shared Docker network `quant-network`, alongside the `quant-infra-db` stack (`quant-postgres`, `quant-mongo`, `quant-redis`).

**Current state:** the codebase is still the `python-template` skeleton (`src/main.py` is a stub; `pyproject.toml` still has `name = "python-template"` and no runtime dependencies). The intended build-out is laid out phase-by-phase in `docs/plans/ROADMAP.md` — consult it before adding non-trivial code so changes match the planned architecture (FastAPI app under `src/main.py` with async lifespan, Pydantic Settings in `src/config.py`, schemas under `src/schemas/`, routers under `src/api/v1/`).

## Commands

Every Python invocation must be prefixed with `uv run`. Never `python`, `pip`, `poetry`, or `conda` directly.

**Quality gate (run all four before any commit):**
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest
```

**Individual:**
- `uv run pytest -v` — full test suite (coverage gate ≥80% via `pyproject.toml`)
- `uv run pytest tests/<path>::<test_name> -v` — single test
- `uv run pytest --cov=src --cov-report=term-missing` — coverage report
- `uv run mypy src tests` — strict type check (`mypy` is configured `strict = true`)
- `uv run ruff check .` / `uv run ruff format .` — lint / auto-format
- `uv run pre-commit run --all-files` — run all hooks locally
- `uv run python -m src.main` — run the entrypoint
- `uv run bandit -r src` / `uv run pip-audit` — security scans

**Dependencies:**
- `uv sync --all-groups` — install (`uv.lock` is committed and authoritative)
- `uv add <pkg>` / `uv add --dev <pkg>` — add deps
- `uv lock --upgrade-package <pkg> && uv sync` — bump one package

**Docker:**
- `docker build -t quant-api-gateway:dev .`
- `docker run --rm quant-api-gateway:dev`

A more exhaustive command catalogue lives in `.claude/knowledge/commands.md`.

## Architecture & hard rules

Data flow is layered and one-way — lower layers must not import from higher ones:

```
External I/O → src/data → src/core → src/api → src/cli / src/main.py
```

Application entrypoints (`api/`, `cli/`, `main.py`) may import from `src/`; `src/` modules must not import from entrypoint layers. Each subpackage owns its own `errors.py` inheriting from a single root exception.

**Hard rules (from `.claude/knowledge/project-skill.md`) — these are enforced:**
1. **`uv run` everywhere** — no bare `python`/`pip`.
2. **Async-first I/O at boundaries.** All HTTP via `httpx.AsyncClient` with explicit `timeout=`. `requests` is forbidden in library code (it blocks the event loop). Sync internal compute is fine.
3. **Pydantic v2 at boundaries.** Data crossing module/process boundaries is a Pydantic model, never a raw dict. Validation rejects malformed input before service logic runs.
4. **Full type annotations** on every public function (args + return). No bare `Any` — if unavoidable, justify in a comment. Prefer `Sequence`/`Mapping`/`Iterable` for params; concrete `list`/`dict` for returns.
5. **Logging, not `print`.** `logger = logging.getLogger(__name__)` at module top. Use `%`-formatting for deferred interpolation (`logger.info("processed %d items", n)`). Never log secrets, tokens, or full request bodies.
6. **Config via `pydantic-settings`** reading env vars (or `.env` for local dev). No hard-coded paths — all base paths come from a single `Settings` object.
7. **Timestamps in UTC** internally; localize only at presentation boundaries.
8. **Conventional Commits** for messages (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).

File-size target: ≤500 lines per `.py` file; split into a package when exceeded. Imports are ruff-isort sorted (stdlib → third-party → local); no relative imports beyond one level; no wildcard imports.

## Tests

- `tests/` mirrors `src/` structure — one test file per source file.
- `pytest-asyncio` is configured in `asyncio_mode = "auto"`, so `async def test_…` works without per-test markers.
- No network in unit tests; gate integration tests behind markers.
- Coverage gate `--cov-fail-under=80` is wired into `pyproject.toml`'s default pytest addopts — running `uv run pytest` enforces it locally too, not just in CI.

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
- `docs/plans/ROADMAP.md` — phased build-out of the gateway service (currently the source of truth for what to build next)

## What this project deliberately doesn't use

`requests` (sync — use `httpx`), `poetry`/`pip-tools`/`conda` (replaced by `uv`), `flake8`/`isort`/`black` (replaced by `ruff`). Don't reintroduce these.
