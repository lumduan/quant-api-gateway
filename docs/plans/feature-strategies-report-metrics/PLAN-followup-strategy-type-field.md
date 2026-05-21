# Follow-up — quant-api-gateway — Strategy `type` field on registry response

| Field | Value |
|---|---|
| Track | Follow-up to `feature-strategies-report-metrics` (post-Phase 3) |
| Date | 2026-05-21 |
| Author | Claude (Opus 4.7), acting on lumduan's behalf |
| Branch | `feat/strategy-registry-type-field` |
| Target | `main` |
| Linked roadmap | `../../../plans/feature-strategies-report-metrics/ROADMAP.md` |
| Companion plans | `strategies/csm-set/docs/plans/feature-strategies-report-metrics/PLAN-followup-http-ingestion-migration.md`, `quant-dashboard/docs/plans/feature-strategies-report-metrics/PLAN-followup-strategy-type-verification.md` |
| Plan file location (in repo) | `docs/plans/feature-strategies-report-metrics/PLAN-followup-strategy-type-field.md` |

---

## Context

Phase 4 of the report-metrics feature (`quant-dashboard`) was implemented
under the assumption — documented in `quant-dashboard/CLAUDE.md` — that
`StrategyInfo.type` is the dispatch key for the dashboard's
`StrategyAdapterFactory.ADAPTER_MAP`. The factory dispatches on
`strategy.type` (e.g. `"EQUITY_MOMENTUM"` → `CSMSetAdapter`) and falls back
to `DefaultAdapter` when `type` is absent.

During end-to-end verification on 2026-05-21 the dashboard rendered with
the warning `Strategy type "(unknown)" has no adapter — falling back to
generic metrics`. The cause is that the gateway's response from
`GET /api/v1/strategies` does **not** carry a `type` field at all:

- `src/schemas/registry.py::StrategyConfig` has no `type` field.
- `strategies.json` shipped in the container has no `type` key.
- `src/api/v1/strategies.py` returns `StrategyConfig` directly as the
  response model — so adding the field to `StrategyConfig` is sufficient
  to surface it on the wire.

This follow-up closes the registry-side gap so the dashboard's
registry-based adapter dispatch — explicitly mandated by the dashboard's
hard rule "Adding a strategy type is one line in `ADAPTER_MAP` — do not
introduce a switch" — actually works.

It is intentionally small: one Pydantic field, one JSON key, schema
docs, and tests. It does **not** touch the ingestion path, the
`strategy_report_snapshot` write path, or any aggregation logic.

---

## Scope

### In scope

1. **`src/schemas/registry.py`** — add `type: str` to `StrategyConfig`
   with `min_length=1` and a docstring that names the dashboard's
   `ADAPTER_MAP` as the consumer. Field is **required** (not optional) so
   misconfigured registries fail fast at startup, not silently in the
   dashboard. Existing `StrategyRegistry` and helpers are unchanged.
2. **`strategies.json`** — add `"type": "EQUITY_MOMENTUM"` to the
   `csm-set` entry. The literal `"EQUITY_MOMENTUM"` is the key currently
   registered in
   `quant-dashboard/src/components/strategy/StrategyAdapterFactory.tsx`
   line 15.
3. **`tests/schemas/test_registry.py`** — extend existing tests:
   - `type` field is required and rejects empty string.
   - Existing happy-path test includes `type` in its `_config` builder.
4. **`tests/services/test_strategy_registry.py`** —
   `_test_registry_payload` fixtures gain `type`.
5. **`tests/api/v1/test_strategies.py`** — assert the
   `GET /api/v1/strategies` response includes `type` and that
   `GET /api/v1/strategies/{id}` also exposes it.
6. **`tests/api/v1/test_strategies_performance.py`** — fixtures that
   instantiate `StrategyConfig` directly get a `type` argument.
7. **`tests/services/test_snapshot_writer.py`** — `_cfg` helper gains
   `type`.
8. **`tests/conftest.py`** — `load_test_registry` fixture (if it builds a
   registry inline) gains `type` on each entry; otherwise no change.

### Out of scope

- Any change to the ingestion path, `strategy_report_snapshot`, or the
  `extended_data.report` payload. Those are covered by the companion
  plan in `strategies/csm-set/docs/plans/.../PLAN-followup-http-ingestion-migration.md`.
- Provisioning a `gateway_ro` Postgres role (still owed by
  `quant-infra-db`; tracked separately).
- Adapter registration for future strategy types (TFEX etc.) — adding a
  new `type` value to the dashboard map is one line and lives in the
  dashboard repo when that strategy lands.
- Changing the dashboard's behaviour when `type` is missing — it
  correctly falls back to `DefaultAdapter` and we want to keep that
  defensive behaviour for forward-compatibility.

---

## Deliverables

### Created

- `docs/plans/feature-strategies-report-metrics/PLAN-followup-strategy-type-field.md`
  (this plan).

### Modified

- `src/schemas/registry.py` — `+1` field on `StrategyConfig`.
- `strategies.json` — `+1` key on the `csm-set` entry.
- `tests/schemas/test_registry.py` — fixtures + one new test.
- `tests/services/test_strategy_registry.py` — registry payload fixtures.
- `tests/services/test_snapshot_writer.py` — `_cfg` helper.
- `tests/api/v1/test_strategies.py` — assertions on response shape.
- `tests/api/v1/test_strategies_performance.py` — local fixtures only.
- `tests/conftest.py` — only if the in-repo registry payload is built
  there (verify before editing).

### Untouched (do not modify in this PR)

- `src/api/v1/strategies.py` — no handler change required; the response
  model is `StrategyConfig` itself, so the new field is surfaced
  automatically.
- `src/api/v1/router.py`, `src/api/v1/performance.py`,
  `src/api/v1/portfolio.py`, `src/api/v1/strategy_report.py`,
  `src/api/v1/ingest.py` — unrelated.
- `src/services/aggregator.py`, `snapshot_writer.py`,
  `cache_invalidator.py` — they iterate over registry entries but never
  read `type`.
- `.env.example`, `Dockerfile`, `docker-compose.yml` — no env or runtime
  change.
- `src/schemas/strategy.py::StrategyMetadata.type` — that is the
  ingestion-payload field (already required and unrelated). Leave alone.

---

## Implementation Order

1. **Branch**: `git checkout -b feat/strategy-registry-type-field`.
2. **Test-first**: extend `tests/schemas/test_registry.py` —
   - parametrised "missing type" / "empty type" cases that expect
     `ValidationError`,
   - one assertion that `StrategyConfig(...).type == "EQUITY_MOMENTUM"`.
   Watch them fail.
3. **`src/schemas/registry.py`** — add the field:
   ```python
   type: str = Field(
       description=(
           "Strategy type discriminator consumed by the dashboard's "
           "StrategyAdapterFactory (e.g. ``EQUITY_MOMENTUM`` → CSMSetAdapter). "
           "Required so misconfigured registries fail at startup."
       ),
       min_length=1,
   )
   ```
4. **`strategies.json`** — add `"type": "EQUITY_MOMENTUM"` to the
   `csm-set` entry. Re-run the schema test that loads the file to confirm
   it still parses.
5. **Fixture sweep** — search for every `StrategyConfig(` /
   `StrategyConfig.model_validate(` call site under `tests/` and add
   `type` to the kwargs / dict. Use `rg "StrategyConfig\b" tests/`.
6. **API test** — in `tests/api/v1/test_strategies.py` add one assertion
   to the list-endpoint test and one to the by-id-endpoint test:
   `assert payload["type"] == "EQUITY_MOMENTUM"`.
7. **Quality gate**:
   ```bash
   uv run ruff check . \
     && uv run ruff format --check . \
     && uv run mypy src tests \
     && uv run pytest
   ```
   Coverage must stay ≥ 90% (already gated by `pyproject.toml`).
8. **Manual verification** (after rebuilding the container):
   ```bash
   docker compose up -d --force-recreate api-gateway
   curl -s http://localhost:8080/api/v1/strategies | jq '.[0].type'
   # → "EQUITY_MOMENTUM"
   ```
9. **Commit**: single conventional commit
   `feat(gateway): expose strategy type field on registry response`.
10. **PR description** must link this plan and the dashboard companion
    plan so the reviewer sees the full motivation.

---

## Critical Files (reuse rather than recreate)

- `src/schemas/registry.py` — small file (≈ 50 lines). The new field
  follows the exact pattern of the existing `name` field (`str`,
  `min_length=1`, descriptive `Field(...)`).
- `src/api/v1/strategies.py` lines 28, 42 — the `response_model` is
  already `StrategyConfig` / `list[StrategyConfig]`, so FastAPI surfaces
  the new field with no handler edit.
- `tests/schemas/test_registry.py` — `_config()` helper at line 10 is the
  central fixture factory; updating it once cascades to most tests.
- `tests/api/v1/test_strategies.py` — uses `load_test_registry`; the
  fixture in `tests/conftest.py` is the single source of truth for the
  test-mode `strategies.json`. Verify whether it inlines the JSON or
  loads from disk before editing.

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Making `type` required breaks any operator who already deployed `strategies.json` from this repo without the field | The shipped `strategies.json` is updated in the same commit; the field is required on purpose so misconfiguration fails at startup with a clear Pydantic message, not silently in the UI. A startup error is strictly easier to diagnose than a silent "(unknown)" fallback in the browser. |
| Tests scattered across the suite build `StrategyConfig` inline and break en-masse | Resolved by step 5 (fixture sweep). The `rg "StrategyConfig\b" tests/` pre-flight catches them all before changing the schema. |
| The literal `"EQUITY_MOMENTUM"` is the wrong dashboard key | Verified at write-time: the dashboard's `StrategyAdapterFactory.tsx:15` maps `EQUITY_MOMENTUM` → `CSMSetAdapter` and the test fixture at `StrategyAdapterFactory.test.tsx:45` confirms `'routes EQUITY_MOMENTUM strategies to CSMSetAdapter'`. If the key is ever renamed, both repos must change in lock-step — call this out in the PR description. |
| Dashboard fetches are cached by TanStack Query (`useOverallPerformance` `staleTime` 4.5 min); type change won't show until cache expiry | Acceptable — operator can force-refresh once. Documented in the dashboard verification plan. |
| Gateway response shape change is a breaking change for any other consumer that strictly validates the response | The dashboard is the only known consumer; the gateway's `StrategyConfig` is internal. Treat the response model as additive (new required field). External consumers (none today) would have to update their Pydantic models. Note this in the PR. |

---

## Acceptance Criteria

- [ ] `uv run ruff check .` — zero findings.
- [ ] `uv run ruff format --check .` — no drift.
- [ ] `uv run mypy src tests` — zero strict-mode errors.
- [ ] `uv run pytest` — green, coverage ≥ 90%.
- [ ] `GET /api/v1/strategies` returns a payload whose first element has
      `"type": "EQUITY_MOMENTUM"`.
- [ ] `GET /api/v1/strategies/csm-set` returns the same `type` value.
- [ ] Bringing up the gateway with a `strategies.json` that **omits**
      `type` raises a `pydantic.ValidationError` at startup, naming the
      missing field. (Negative integration verification.)
- [ ] Dashboard, after a force-refresh, no longer shows the
      `Strategy type "(unknown)"` warning; CSMSetAdapter renders.
- [ ] Single conventional commit:
      `feat(gateway): expose strategy type field on registry response`.

---

## Verification Plan

```bash
# 1. Quality gate
git checkout feat/strategy-registry-type-field
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest

# 2. Wire-level check
docker compose up -d --force-recreate api-gateway
curl -s http://localhost:8080/api/v1/strategies | jq '.'
# Expect: [{"id":"csm-set","name":"CSM SET Strategy","type":"EQUITY_MOMENTUM",...}]

# 3. Negative startup check (manual)
#    Edit strategies.json to remove "type", restart container,
#    confirm logs show ValidationError naming `type` as missing.

# 4. End-to-end (delegated to dashboard verification plan)
#    See quant-dashboard/docs/plans/.../PLAN-followup-strategy-type-verification.md
```

---

## Follow-ups (intentionally not included here)

- **csm-set HTTP ingestion migration** — switching csm-set from direct
  `db_gateway` writes to the documented `POST /api/v1/ingest/daily-report`
  contract. Tracked in
  `strategies/csm-set/docs/plans/feature-strategies-report-metrics/PLAN-followup-http-ingestion-migration.md`.
  That is the work that actually unblocks the dashboard's **Report** tab
  (this plan only unblocks the adapter dispatch / **Metrics** tab).
- **`gateway_ro` role provisioning** — still owed by `quant-infra-db`.
  Out of scope here.
