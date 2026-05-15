# Aggregator Reference

**Module:** `src.services.aggregator`
**Available since:** v0.1.0 (Phase 4)

Pure functions for computing weighted return, combined drawdown, and merged equity curves. No I/O — all functions accept Pydantic models and return plain types.

---

## Import

```python
from src.services.aggregator import (
    calculate_weighted_return,
    merge_equity_curves,
    calculate_combined_drawdown,
)
```

---

## `calculate_weighted_return()`

Compute the capital-weighted daily return across strategies.

### Signature

```python
def calculate_weighted_return(
    strategies: Sequence[StrategyPerformanceResponse],
    weights: Mapping[str, float],
) -> float: ...
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `strategies` | `Sequence[StrategyPerformanceResponse]` | required | Latest performance snapshot for each active strategy |
| `weights` | `Mapping[str, float]` | required | Map of `strategy_id → capital weight` |

### Returns

`float` — Weighted daily return in fractional form (`0.0148` ⇒ 1.48%). Returns `0.0` when `sum(weights.values()) <= 0` or `strategies` is empty.

### Formula

`Σ (daily_pnl_i / total_value_i) × weight_i / Σ weights`

Strategies with `total_value <= 0` are excluded from the numerator. The denominator is always `sum(weights.values())`.

### Example

```python
from decimal import Decimal
from datetime import UTC, datetime
from src.schemas.gateway import StrategyPerformanceResponse

s = [
    StrategyPerformanceResponse(
        strategy_id="a",
        daily_pnl=Decimal("1000"),
        total_value=Decimal("100000"),
        max_drawdown=Decimal("-0.01"),
        sharpe_ratio=Decimal("1.0"),
        last_updated=datetime(2026, 5, 15, tzinfo=UTC),
    )
]
result = calculate_weighted_return(s, {"a": 1.0})
assert result == 0.01
```

---

## `merge_equity_curves()`

Merge per-strategy equity curves into a single portfolio curve.

### Signature

```python
def merge_equity_curves(
    curves: Mapping[str, Sequence[EquityPoint]],
    weights: Mapping[str, float],
    normalize: bool = True,
) -> list[EquityPoint]: ...
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `curves` | `Mapping[str, Sequence[EquityPoint]]` | required | Map of `strategy_id → equity_curve` |
| `weights` | `Mapping[str, float]` | required | Map of `strategy_id → capital weight` |
| `normalize` | `bool` | `True` | Normalize each curve to base 100 before merging. `False` preserves raw values. |

### Returns

`list[EquityPoint]` — Merged curve covering every date with at least one contributing strategy. Values are `Decimal` with 4 decimal places. Empty list if no curve qualifies.

### Algorithm

1. Drop strategies with empty curves, weight ≤ 0, or first value ≤ 0
2. Normalize each curve to base 100 (unless `normalize=False`)
3. Outer-join on date strings; forward-fill missing dates
4. Per-row weighted sum divided by sum of present-strategy weights

### Example

```python
from src.schemas.strategy import EquityPoint
from decimal import Decimal

curves = {
    "a": [EquityPoint(date="2026-05-01", value=Decimal("1000")),
          EquityPoint(date="2026-05-02", value=Decimal("1100"))],
}
# normalize=True (default): values normalized to 100, 110
result = merge_equity_curves(curves, {"a": 1.0})
assert result[0].value == Decimal("100.0000")

# normalize=False: raw values preserved
result = merge_equity_curves(curves, {"a": 1.0}, normalize=False)
assert result[0].value == Decimal("1000.0000")
```

---

## `calculate_combined_drawdown()`

Compute the maximum drawdown of the merged portfolio equity curve.

### Signature

```python
def calculate_combined_drawdown(
    curves: Mapping[str, Sequence[EquityPoint]],
    weights: Mapping[str, float],
) -> float: ...
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `curves` | `Mapping[str, Sequence[EquityPoint]]` | required | Map of `strategy_id → equity_curve` |
| `weights` | `Mapping[str, float]` | required | Map of `strategy_id → capital weight` |

### Returns

`float` — Max drawdown in fractional form (e.g. `-0.063` ⇒ −6.3%). Returns `0.0` when the merged curve is empty or never declines.

### Algorithm

Calls `merge_equity_curves()` internally, then single-pass O(n) scan: `min(value / running_peak - 1)`. Returns negative value matching `max_drawdown` convention.

### Example

```python
curves = {
    "a": [EquityPoint(date="2026-05-01", value=Decimal("100")),
          EquityPoint(date="2026-05-02", value=Decimal("90")),
          EquityPoint(date="2026-05-03", value=Decimal("110"))],
}
drawdown = calculate_combined_drawdown(curves, {"a": 1.0})
# Peak=100, trough=90: 90/100 - 1 = -0.10
assert drawdown == pytest.approx(-0.10)
```

---

## See Also

- [Strategy Performance Response Schema](../schemas/gateway.md) — `StrategyPerformanceResponse` model
- [EquityPoint Schema](../schemas/strategy-payload.md) — `EquityPoint` model
- [Performance Service](performance.md) — calls aggregator with DB data
