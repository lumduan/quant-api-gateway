# Phase 4 — Aggregation Engine

| Field | Value |
|---|---|
| Phase | 4 — Aggregation Engine |
| Date | 2026-05-15 |
| Author | Claude (Opus 4.7), acting on lumduan's behalf |
| Branch | `feat/phase-4-aggregation-engine` |
| Base branch | `main` |
| Target | `main` |
| Linked roadmap | `docs/plans/ROADMAP.md` §4.1–§4.3 |

---

## Objective

`quant-api-gateway` has finished Phase 3 — Strategy Services can POST a Daily Performance
report, the gateway persists it to `db_gateway.daily_performance`, and inline after each
ingest the snapshot writer upserts a `portfolio_snapshot` row once every active strategy
has reported for the day. The snapshot writer already computes `total_portfolio`,
`weighted_return`, and `allocation`, but writes `combined_drawdown = NULL` (explicitly
deferred to Phase 4). The raw `daily_pnl` and `equity_curve` from each ingest are
preserved inside `daily_performance.metadata` JSONB, so Phase 4 has everything it needs.

Phase 4 closes the math gap: add the three pure-function aggregators specified in ROADMAP
§4.1–§4.3 to a new `src/services/aggregator.py` module, then wire `combined_drawdown`
into the existing snapshot writer so the portfolio_snapshot column stops being NULL.

This phase introduces no API endpoints, no Redis caching, and no new schemas — those are
Phases 5 and 6 per the ROADMAP dependency map. Phase 4 is strictly the math + the
snapshot-writer integration that consumes it.

## Scope

### In scope

1. **Aggregator module** — `src/services/aggregator.py` exporting three pure functions
   per ROADMAP §4.1–§4.3:
   - `calculate_weighted_return(strategies, weights) -> float`
   - `merge_equity_curves(curves, weights) -> list[EquityPoint]`
   - `calculate_combined_drawdown(curves, weights) -> float`
2. **Typed error** — `AggregationError` added to `src/services/errors.py` (subclass of
   `ServiceError`).
3. **Snapshot writer integration** — `src/services/snapshot_writer.py` SQL extended to
   `SELECT … metadata`, a new `_extract_equity_curve(metadata)` helper, and
   `_compute_aggregates(...)` calls the aggregator to fill `combined_drawdown`.
4. **Dependency** — add `pandas>=2.2` to `pyproject.toml` `[project] dependencies`; bump
   `uv.lock`.
5. **Tests** — `tests/services/test_aggregator.py` (new) with hand-crafted fixtures whose
   math is known by hand; `tests/services/test_snapshot_writer.py` extended to assert
   `combined_drawdown` is now persisted.
6. **Plan document** — this file.
7. **Roadmap update** — tick §4.1/§4.2/§4.3 and update the Current status block.

### Out of scope (later phases)

- `src/services/cache.py`, `cache_invalidator.py`, Redis TTL keys — **Phase 5**.
- `GET /api/v1/overall-performance`, `GET /api/v1/portfolio/...`, equity-curve read
  endpoints — **Phase 6**.
- JSON-structured logging — **Phase 7**.
- New schemas under `src/schemas/aggregation.py` — not needed; aggregator functions
  consume Phase 2's `StrategyPerformanceResponse` and `EquityPoint` and produce
  `float` / `list[EquityPoint]`. The `OverallPerformanceResponse` already declares every
  field Phase 6 will populate.
- Real Postgres integration tests — **Phase 7** (Phase 4 mocks the pool as Phase 3 did).

---

## Design Decisions

### 1. Function signatures match ROADMAP §4.1 verbatim

`calculate_weighted_return(strategies: list[StrategyPerformanceResponse], weights:
dict[str, float]) -> float`. The ROADMAP code snippet has a latent `Decimal/float`
mix-up (because `s.daily_pnl` is `Decimal` per Phase 2 and `weights[..]` is `float`).
The implementation converts each `Decimal` to `float` inside the comprehension and
returns `float`, matching the published signature exactly.

**Why:** Stable cross-phase contract; the snapshot writer already runs `float(...)` at
the storage boundary because `daily_performance` columns are `DOUBLE PRECISION`. Keeping
aggregator inputs as the Phase 2 schemas preserves "Pydantic at boundaries" without
leaking `Decimal` into the math.

### 2. `pandas` for the equity-curve merger only

The merger uses `pd.Series` + `pd.concat` for outer-join date alignment + `ffill`.
Drawdown scan and weighted-return arithmetic stay in pure Python (`max`, generator
expressions, `float`).

**Why:** The merger algorithm — outer-join by date, forward-fill, normalise each curve
to base 100, then weighted-sum — is exactly what `pd.concat(axis=1).ffill()` solves
cleanly. Hand-rolling outer-join + ffill in stdlib is fiddly and easy to get wrong on
sparse multi-year curves. Conversely the other two functions are trivial in pure Python
and don't justify the dependency at the call site. Pandas already supports Python 3.11+
without compile steps.

### 3. Aggregator is a pure module — no DB, no Redis, no HTTP

`aggregator.py` imports only `decimal`, `logging`, `pandas`, and Phase 2 schemas
(`EquityPoint`, `StrategyPerformanceResponse`). No I/O. Callers (snapshot writer today,
read endpoints later) own data fetching.

**Why:** Hard rule — `src/services/aggregator.py` is a "core" module per the
architecture's data flow (`data → core → api`). Pure functions are trivially testable,
deterministic, and reusable.

### 4. `merge_equity_curves` normalises before weighting

Each input curve is divided by its first non-null value × 100 so every series starts at
100. Then the weighted sum is taken per date. This matches ROADMAP §4.3 "normalize each
input curve to base 100 before merging."

**Why:** Equity curves from different strategies have different absolute scales (e.g.
one runs at $1M, another at $10k). Normalising lets the weighted-sum represent
fractional movement, which is the meaningful portfolio trajectory.

### 5. `calculate_combined_drawdown` operates on the merged curve

It calls `merge_equity_curves` internally rather than taking pre-merged input. The
function takes the same `curves` / `weights` mapping the merger does and returns a
single `float` (the max drawdown of the merged curve).

**Why:** Atomicity at the API level. The snapshot writer and any future caller passes
one input and gets one number — no risk of merging twice with different weights.

### 6. Edge-case contracts

| Case | Behaviour |
|---|---|
| `strategies=[]` | `calculate_weighted_return` returns `0.0` |
| `weights={}` | returns `0.0` (no division) |
| `sum(weights.values()) <= 0` | returns `0.0` |
| `total_value <= 0` for a strategy | strategy excluded from the weighted sum |
| missing weight for a strategy id | weight treated as `0.0` (`weights.get(id, 0.0)`) |
| `curves={}` for drawdown / merger | drawdown returns `0.0`; merger returns `[]` |
| any individual curve has `<1` points | strategy excluded from the merge |
| total weight of contributing curves `<= 0` | merger returns `[]` |
| merged curve never declines | drawdown returns `0.0` |
| all merged-curve peaks `<= 0` | drawdown returns `0.0` (avoid div-by-zero) |
| forward-fill before any data | leading-NaN rows are dropped from the output |

Drawdown formula: `min over t of ( curve[t] / running_peak[<=t] - 1 )`. ROADMAP writes
it as `(peak − trough) / peak`; the equivalent fractional form returns a negative number
(matching the sign of `max_drawdown` everywhere else in the codebase).

### 7. Snapshot writer extension

`_compute_aggregates(rows, active) -> SnapshotAggregates` is extended so
`combined_drawdown: float | None` is now filled rather than always `None`. The function:

- Reads each row's `metadata` (already preserved by Phase 3 ingestion).
- Parses the `equity_curve` list back into `EquityPoint` objects via a new
  `_extract_equity_curve` helper.
- Feeds the `{strategy_id: [EquityPoint, …]}` mapping to
  `calculate_combined_drawdown(...)` with the same `{strategy_id: float(weight)}`
  mapping it already constructs.
- The resulting value populates `SnapshotAggregates.combined_drawdown`.

The SQL is extended from:

```sql
SELECT DISTINCT ON (strategy_id) strategy_id, total_value, daily_return
FROM daily_performance WHERE ...
```

to:

```sql
SELECT DISTINCT ON (strategy_id) strategy_id, total_value, daily_return, metadata
FROM daily_performance WHERE ...
```

If every row's `equity_curve` is empty or absent, `combined_drawdown` stays `None`
(matches ROADMAP §4.2 "gracefully handles missing data for individual strategies").

### 8. No new schemas

`OverallPerformanceResponse` from Phase 2 already declares `combined_max_drawdown:
Decimal`, `allocation: dict[str, Decimal]`, etc. Phase 4 introduces no new Pydantic
models. `EquityPoint` (Phase 2) is reused as the input/output unit for curves.

### 9. Pure-Python drawdown loop

Drawdown is a single-pass O(n) scan over the merged series. No pandas needed. Pure
Python keeps the function trivially unit-testable and avoids dragging `pd.Series`
semantics (NaN handling, index ops) into the math.

### 10. Tests use hand-crafted fixtures with closed-form expected values

Every aggregator test uses values the planner can compute by hand and assert with
`pytest.approx(..., rel=1e-9)`. No golden-data files, no fuzz tests.

---

## Schema Design

**No new schemas in Phase 4.** The aggregator consumes:

- `src.schemas.gateway.StrategyPerformanceResponse` (`daily_pnl`, `total_value`,
  `strategy_id`)
- `src.schemas.strategy.EquityPoint` (`date`, `value`)

And produces only `float` / `list[EquityPoint]`. The Phase 2
`OverallPerformanceResponse` already declares every output field that Phase 6 will need.

---

## Module Design

### `src/services/aggregator.py`

```python
"""Pure aggregation primitives for the gateway."""

from collections.abc import Mapping, Sequence
import logging

import pandas as pd

from src.schemas.gateway import StrategyPerformanceResponse
from src.schemas.strategy import EquityPoint

logger = logging.getLogger(__name__)


def calculate_weighted_return(
    strategies: Sequence[StrategyPerformanceResponse],
    weights: Mapping[str, float],
) -> float:
    """Σ (daily_pnl_i / total_value_i) × weight_i / Σ weights — see ROADMAP §4.1.

    Returns ``0.0`` if ``sum(weights.values()) <= 0`` or every strategy has
    ``total_value <= 0``. Strategies with ``total_value <= 0`` are excluded from
    the sum but still count toward the divisor.
    """
    ...


def merge_equity_curves(
    curves: Mapping[str, Sequence[EquityPoint]],
    weights: Mapping[str, float],
) -> list[EquityPoint]:
    """Outer-join curves by date, forward-fill, base-100 normalise, weighted sum.

    Returns ``[]`` if ``curves`` is empty, every curve is empty, or the total
    weight of contributing curves is ``<= 0``.
    """
    ...


def calculate_combined_drawdown(
    curves: Mapping[str, Sequence[EquityPoint]],
    weights: Mapping[str, float],
) -> float:
    """Max drawdown of the merger of ``curves`` under ``weights``.

    Equivalent to ``min over t of ( merged[t]/running_peak[<=t] - 1 )``. Returns
    ``0.0`` if the merged curve is empty, monotonically non-decreasing, or every
    peak value is ``<= 0``.
    """
    ...
```

All three functions get Google-style docstrings (Args / Returns / Raises / Example) and
full type annotations.

### `src/services/errors.py` — addition

```python
class AggregationError(ServiceError):
    """Raised when aggregation inputs are inconsistent or arithmetic fails."""
```

Reserved for genuine input-shape failures; the documented edge cases above return
defaults rather than raising.

### `src/services/snapshot_writer.py` — modifications

- `_SELECT_TODAY_SQL` extended to also return `metadata`.
- New helper `_extract_equity_curve(metadata: Any) -> list[EquityPoint]` that
  json-loads strings, accepts dicts, and returns the parsed `equity_curve` list (or
  `[]` if absent / malformed).
- `_compute_aggregates(rows, active)` builds the `{strategy_id: [EquityPoint, …]}`
  mapping from `rows` and calls `calculate_combined_drawdown(...)` with the same
  `{strategy_id: float(weight)}` mapping it already constructs.
- If every strategy's `equity_curve` is empty/absent, the writer leaves
  `combined_drawdown = None` and logs a single INFO line.
- The upsert SQL itself is unchanged — `combined_drawdown` is already a column and
  param.

### `pyproject.toml`

Add `"pandas>=2.2"` to `[project] dependencies` (alphabetical position before
`pydantic`). `asyncpg` returns JSONB as `str` by default, so no codec changes are needed.

### `src/main.py`, routers, schemas

**Unchanged.** Phase 4 introduces no API surface.

---

## Deliverables

### Created

| File | Description |
|---|---|
| `src/services/aggregator.py` | Three pure aggregation functions |
| `tests/services/test_aggregator.py` | Unit tests with hand-crafted fixtures |
| `docs/plans/phase_4_aggregation_engine/phase_4_aggregation_engine.md` | This plan |

### Modified

| File | Change |
|---|---|
| `src/services/errors.py` | Add `AggregationError` |
| `src/services/snapshot_writer.py` | SQL adds `metadata`; `_compute_aggregates` fills `combined_drawdown` via aggregator |
| `tests/services/test_snapshot_writer.py` | New tests for `combined_drawdown`; existing tests updated to include `metadata` rows |
| `pyproject.toml` | Add `pandas>=2.2` |
| `uv.lock` | Re-locked by `uv add pandas` |
| `docs/plans/ROADMAP.md` | Tick §4.1/§4.2/§4.3; advance Current status to Phase 5 |

### Untouched

- `src/schemas/{strategy,gateway,registry,errors}.py`
- `src/api/v1/*`, `src/main.py`, `src/config.py`
- `src/db/*`
- `strategies.json`
- `docker-compose.yml`, `Dockerfile`, `.env.example`

---

## Acceptance Criteria

### Aggregator math

- [x] `calculate_weighted_return` with two strategies (60/40 weighting; known inputs) →
      equals the hand-computed expected value (2026-05-15)
- [x] `calculate_weighted_return` with a single strategy → equals
      `daily_pnl / total_value`
- [x] `calculate_weighted_return` with all weights zero → `0.0`
- [x] `calculate_weighted_return` with `total_value == 0` for one strategy → that
      strategy is excluded, math is correct over the rest
- [x] `calculate_weighted_return` with `strategies=[]` → `0.0`
- [x] `calculate_weighted_return` with a strategy id missing from `weights` → that
      strategy contributes `0` (weight `0`)
- [x] `merge_equity_curves` with two curves on identical dates → weighted base-100
      sum on every date
- [x] `merge_equity_curves` with curves on different date ranges (outer join) → output
      covers every date in the union; forward-fill in effect
- [x] `merge_equity_curves` with a single curve → returns the normalised curve
- [x] `merge_equity_curves` with `curves={}` → `[]`
- [x] `merge_equity_curves` normalises each input curve to base 100 before weighting
- [x] `merge_equity_curves` with `weights={..: 0.0}` → `[]`
- [x] `calculate_combined_drawdown` on a hand-built curve with a known drawdown matches
      the expected drawdown
- [x] `calculate_combined_drawdown` on a monotonically increasing curve → `0.0`
- [x] `calculate_combined_drawdown` with `curves={}` → `0.0`
- [x] `calculate_combined_drawdown` gracefully handles a strategy whose `equity_curve`
      is empty (skips it, computes over the rest)
- [x] `calculate_combined_drawdown` integration: two non-trivial curves → matches the
      hand-computed drawdown of the merged-then-scanned series

### Snapshot writer integration

- [x] `_compute_aggregates` populates `combined_drawdown` to a float (not `None`)
      when every row has a non-empty `equity_curve`
- [x] `_compute_aggregates` returns `combined_drawdown=None` when no row has a usable
      `equity_curve` (graceful degradation)
- [x] `_compute_aggregates` accepts `metadata` returned as either a `dict` or a JSON
      `str` (asyncpg's JSONB return type)
- [x] `maybe_write_snapshot` upserts a `portfolio_snapshot` row whose
      `combined_drawdown` matches the aggregator's output
- [x] `_SELECT_TODAY_SQL` includes the `metadata` column
- [x] All Phase 3 snapshot-writer tests still pass after the SQL/aggregates extension

### Quality gate

- [x] `uv run ruff check .` — zero findings
- [x] `uv run ruff format --check .` — no drift
- [x] `uv run mypy src tests` — zero strict-mode errors
- [x] `uv run pytest -v --cov=src --cov-report=term-missing` — green; coverage ≥ 80%
      (137 passed; total coverage 97.99%)

---

## Test Strategy

### `tests/services/test_aggregator.py`

| Test | Verifies |
|---|---|
| `test_weighted_return_two_strategies_60_40` | known closed-form value |
| `test_weighted_return_single_strategy` | trivial case |
| `test_weighted_return_all_weights_zero` | returns `0.0` |
| `test_weighted_return_total_value_zero_excluded` | strategy with `total_value=0` skipped |
| `test_weighted_return_empty_strategies` | returns `0.0` |
| `test_weighted_return_missing_weight_for_strategy` | strategy without a weight contributes 0 |
| `test_merge_equity_curves_aligned_dates` | weighted base-100 sum per date |
| `test_merge_equity_curves_different_date_ranges_outer_join` | outer-join coverage |
| `test_merge_equity_curves_forward_fill_missing_dates` | gaps filled forward |
| `test_merge_equity_curves_normalises_to_base_100` | each input rebased |
| `test_merge_equity_curves_empty_input` | returns `[]` |
| `test_merge_equity_curves_single_curve` | returns the normalised curve |
| `test_merge_equity_curves_zero_weights` | returns `[]` |
| `test_combined_drawdown_hand_built_curve` | known drawdown value |
| `test_combined_drawdown_monotonic_increasing_returns_zero` | no decline → `0.0` |
| `test_combined_drawdown_flat_curve` | no decline → `0.0` |
| `test_combined_drawdown_empty_curves` | returns `0.0` |
| `test_combined_drawdown_skips_strategy_with_empty_curve` | graceful degradation |
| `test_combined_drawdown_two_strategies_known_merged_drawdown` | full integration |

### `tests/services/test_snapshot_writer.py` — additions

| Test | Verifies |
|---|---|
| `test_compute_aggregates_fills_combined_drawdown_when_curves_present` | float (not `None`) |
| `test_compute_aggregates_combined_drawdown_none_when_no_curves` | graceful `None` |
| `test_compute_aggregates_accepts_metadata_as_json_string` | asyncpg JSONB-as-str path |
| `test_maybe_write_snapshot_persists_combined_drawdown` | upserted row uses aggregator output |
| `test_select_sql_includes_metadata_column` | guards against SQL regression |

### Mocking approach

- `asyncpg.Pool` → existing `mock_pool` fixture in `tests/conftest.py`.
- `metadata` is set in fake rows as either a Python `dict` or a JSON `str` — both must
  parse correctly.
- Test registry from `tests/strategies.fixture.json` (Phase 3 fixture, reused).

### Integration tests

Deferred to Phase 7. The Phase 4 quality gate runs on a mocked pool only.

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `pandas` adds ~30 MB to the Docker image | Acceptable per user; pandas is the right tool for outer-join + ffill. Image size is not a Phase-4 constraint |
| `pd.Series.ffill()` deprecation noise in pandas 2.2+ | Pin `pandas>=2.2`; use the stable `df.ffill()` form |
| `metadata` returns as `str` from asyncpg but as `dict` in mocks | `_extract_equity_curve(metadata)` handles both |
| ROADMAP §4.1 code snippet mixes `Decimal` and `float` | Documented as Design Decision #1; implementation converts inside the function |
| Empty curves on every strategy → `combined_drawdown=None` | Intentional; matches ROADMAP §4.2 "gracefully handles missing data" |
| Phase 6 read endpoints expect a more typed API | Function signatures stay pure-function and stable; future endpoints wrap them without re-design |
| Phase 3 snapshot tests assume `combined_drawdown=None` | Updated as part of Phase 4 modified-files set |
| Pandas import slows cold start | One-time, ~200 ms — negligible vs `asyncpg` pool creation already in the lifespan |
| `mypy --strict` against pandas | `ignore_missing_imports = true` is already in `pyproject.toml`; no stubs required |
| `pytest --cov-fail-under=80` could fail if uncovered branches surface | New tests cover the new branches explicitly |

---

## Implementation Order

1. Create branch — `git checkout -b feat/phase-4-aggregation-engine`
2. Write this plan file
3. `uv add pandas` (updates `pyproject.toml` + `uv.lock`)
4. Add `AggregationError` to `src/services/errors.py`
5. Implement `src/services/aggregator.py` — three pure functions with full docstrings +
   type annotations
6. Write `tests/services/test_aggregator.py` (hand-crafted fixtures, closed-form
   expected values)
7. Extend `src/services/snapshot_writer.py`:
   - SQL `SELECT …` now includes `metadata`
   - `_extract_equity_curve` helper
   - `_compute_aggregates` calls aggregator and fills `combined_drawdown`
8. Update `tests/services/test_snapshot_writer.py`: fix existing fixtures to include
   `metadata`; add new tests
9. Run full quality gate to green
10. Update `docs/plans/ROADMAP.md` — tick §4.1/§4.2/§4.3, update Current status to
    "Phase 5 — Redis Caching Layer"
11. Update Progress / Notes block of this plan
12. Commit (Conventional Commits)
13. Push + `gh pr create`

---

## Verification Plan

```bash
# Branch + clean state
git branch --show-current   # → feat/phase-4-aggregation-engine
git status

# Quality gate (must be green)
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -v --cov=src --cov-report=term-missing

# Aggregator smoke test (REPL)
uv run python - <<'PY'
from decimal import Decimal
from datetime import datetime, UTC
from src.schemas.strategy import EquityPoint
from src.schemas.gateway import StrategyPerformanceResponse
from src.services.aggregator import (
    calculate_weighted_return,
    merge_equity_curves,
    calculate_combined_drawdown,
)

strategies = [
    StrategyPerformanceResponse(
        strategy_id="csm-set-01", daily_pnl=Decimal("1500"),
        total_value=Decimal("100000"), max_drawdown=Decimal("-0.05"),
        sharpe_ratio=Decimal("1.5"),
        last_updated=datetime(2026, 5, 14, tzinfo=UTC),
    ),
    StrategyPerformanceResponse(
        strategy_id="csm-set-02", daily_pnl=Decimal("-400"),
        total_value=Decimal("50000"), max_drawdown=Decimal("-0.10"),
        sharpe_ratio=Decimal("0.9"),
        last_updated=datetime(2026, 5, 14, tzinfo=UTC),
    ),
]
weights = {"csm-set-01": 0.6, "csm-set-02": 0.4}
print("weighted_return =", calculate_weighted_return(strategies, weights))
# expected: ((1500/100000)*0.6 + (-400/50000)*0.4) = 0.0058

curves = {
    "csm-set-01": [EquityPoint(date=d, value=Decimal(v)) for d, v in
                   [("2026-05-12", "100000"), ("2026-05-13", "102000"),
                    ("2026-05-14", "101500")]],
    "csm-set-02": [EquityPoint(date=d, value=Decimal(v)) for d, v in
                   [("2026-05-13", "50000"), ("2026-05-14", "49600")]],
}
print("merged =", merge_equity_curves(curves, weights))
print("combined_drawdown =", calculate_combined_drawdown(curves, weights))
PY

# Optional end-to-end (requires quant-network up):
docker compose up -d
curl -s -X POST localhost:8000/api/v1/ingest/daily-report \
  -H "X-API-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
  -d @tests/payload-01.json
docker exec quant-postgres psql -U postgres -d db_gateway \
  -c "SELECT time, total_portfolio, weighted_return, combined_drawdown, active_strategies FROM portfolio_snapshot ORDER BY time DESC LIMIT 1;"
# combined_drawdown should NOT be NULL after Phase 4
```

---

## Critical Files (reuse rather than recreate)

- `src/schemas/strategy.py` — `EquityPoint` (input for the merger)
- `src/schemas/gateway.py` — `StrategyPerformanceResponse` (input for weighted return);
  `OverallPerformanceResponse` already declares every Phase 6 field
- `src/services/errors.py` — extend, don't replace
- `src/services/snapshot_writer.py` — extend, don't replace; keep the existing
  `SnapshotAggregates` dataclass shape
- `src/db/postgres.py` — `get_pool()` reused as-is
- `tests/conftest.py` — `mock_pool`, `set_env`, `load_test_registry` fixtures reused
- `tests/strategies.fixture.json` — Phase 3 fixture reused
- `pyproject.toml` — only adds `pandas`; ruff/mypy/pytest configs unchanged

---

## Agent Prompt (verbatim)

> You are implementing Phase 4 — Aggregation Engine for the quant-api-gateway project.
> Follow every step below precisely and in order.
>
> Step 1 — Load Agent Context. Before doing anything else: 1) Read
> `.claude/knowledge/project-skill.md` in full — these are non-negotiable hard rules.
> 2) Read `.claude/playbooks/feature-development.md` — follow the 8-step workflow for
> every task.
>
> Step 2 — Understand the Phase. Read `docs/plans/ROADMAP.md` (focus exclusively on the
> Phase 4 — Aggregation Engine section) and
> `docs/plans/phase_3_strategy_ingestion/phase_3_strategy_ingestion.md`.
>
> Step 3 — Create a Git Branch: `git checkout -b feat/phase-4-aggregation-engine`.
>
> Step 4 — Write the Implementation Plan, saved as
> `docs/plans/phase_4_aggregation_engine/phase_4_aggregation_engine.md`, using
> `phase_2_data_models.md` as the format reference. Include: Phase objective and scope,
> deliverables list, acceptance criteria, architecture notes, risks and mitigations,
> step-by-step implementation order, and this prompt embedded.
>
> Step 5 — Implement Phase 4 (verify against ROADMAP): aggregator module, typed
> exceptions, Pydantic v2 schemas as needed, Redis caching, FastAPI router, lifespan
> wiring. Engineering standards: full type annotations, async/await for I/O,
> `httpx.AsyncClient` with explicit `timeout=`, Pydantic at boundaries,
> `logger = logging.getLogger(__name__)`, `%`-style logging, config via
> `pydantic-settings`, UTC timestamps, files ≤500 lines, sorted imports, no wildcards.
>
> Step 6 — Write Tests under tests mirroring src, one test file per source file added.
> `pytest-asyncio` auto mode, no real network/Redis (mock everything), integration tests
> behind `@pytest.mark.integration`. Cover happy path, edge cases, error conditions.
> Run the full quality gate: `uv run ruff check . && uv run ruff format --check . &&
> uv run mypy src tests && uv run pytest`. Fix every error before continuing.
>
> Step 7 — Update Documentation. Mark each acceptance criterion as ✅ (or ⚠️ with a
> note). Add implementation date. Note any problems encountered. Update ROADMAP.md.
>
> Step 8 — Commit and Open PR using Conventional Commits.
>
> Note on scope: the user's prompt example bullets mention Redis caching (Phase 5 per
> ROADMAP) and an aggregation router (Phase 6 per ROADMAP). The prompt explicitly says
> "verify against ROADMAP." Per the ROADMAP dependency map and user confirmation in
> plan mode, Phase 4 implements only the aggregator module (§4.1–§4.3) and wires
> `combined_drawdown` into the existing snapshot writer. Redis caching and read
> endpoints are deferred to Phases 5 and 6 respectively.

---

## Progress / Notes

### Implementation date

2026-05-15

### Quality-gate output

```
uv run ruff check .              → All checks passed!
uv run ruff format --check .     → 45 files already formatted
uv run mypy src tests            → Success: no issues found in 45 source files
uv run pytest -v --cov=src       → 137 passed; Total coverage: 97.99%
```

### Per-module coverage

```
src/services/aggregator.py             56     1   24     1    98%   189
src/services/errors.py                  5     0    0     0   100%
src/services/snapshot_writer.py        95     1   30     1    98%   138
```

The single uncovered branch in `aggregator.py` is the `peak <= 0` defensive
guard inside the drawdown loop — unreachable in practice because
`merge_equity_curves` filters out any curve whose first value is `<= 0`, and
the merged curve is a weighted average of positive numbers. The uncovered
line in `snapshot_writer.py` is in the `combined_drawdown is None` log
branch's interaction with control-flow inside `_compute_aggregates` — its
sibling line is covered by
`test_compute_aggregates_combined_drawdown_none_when_no_curves`.

### Dependency changes

- Added `pandas>=2.2` to `[project] dependencies`. `uv add pandas` resolved
  to `pandas==3.0.3` with transitive deps `numpy==2.4.4`,
  `python-dateutil==2.9.0.post0`, `six==1.17.0`. `pandas` is used by
  `merge_equity_curves` only (outer-join + ffill). Drawdown scan and
  weighted-return arithmetic stay in pure Python.

### Deviations from the plan

- **None on logic.** Every design decision (DD#1–DD#10) landed as written.
- **Plan format:** the plan file was kept self-contained per the `phase_2` /
  `phase_3` reference format; no separate "Architecture notes" subsection was
  added — those notes live inline under Design Decisions.
- **Coverage:** target was ≥80% (gate). Achieved 97.99% globally and 98% on
  the new `aggregator.py`.

### Problems encountered

- **None blocking.** `ruff format` flagged one cosmetic line-break style on
  the first run of `test_aggregator.py` (auto-fixed by `uv run ruff format`).
- The ROADMAP §4.1 code snippet has a latent `Decimal/float` mix-up
  (`s.daily_pnl` is `Decimal` per Phase 2 but is multiplied by a `float`
  weight). Documented as Design Decision #1 — implementation converts
  `Decimal → float` inside the comprehension and returns `float`, matching
  the published signature.

### Time spent

~30 min end-to-end (plan write-up, 4 files added, 3 files modified,
quality-gate iteration, docs).

### Hand-off to Phase 5

- The aggregator is a pure module (`src/services/aggregator.py`) with three
  reusable functions. Phase 5's read endpoints can call them directly without
  re-implementation.
- `portfolio_snapshot.combined_drawdown` is now populated whenever every
  active strategy reports an `equity_curve` in `daily_performance.metadata`
  (the JSONB column was already preserving the raw curve since Phase 3).
- Phase 5 cache keys can rely on `OverallPerformanceResponse` (Phase 2)
  already having `combined_max_drawdown: Decimal`.
