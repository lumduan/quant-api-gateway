"""Pure aggregation primitives for the gateway.

This module exports the three functions specified in ROADMAP §4:

- :func:`calculate_weighted_return` — capital-weighted daily return across strategies.
- :func:`merge_equity_curves` — outer-join + ffill + base-100 normalised weighted sum.
- :func:`calculate_combined_drawdown` — max drawdown of the merged curve.

All functions are pure (no I/O) and accept Pydantic models from
``src.schemas`` rather than raw dicts, preserving the "Pydantic at boundaries"
rule for callers. They internally cast ``Decimal`` to ``float`` because the
``daily_performance`` columns are ``DOUBLE PRECISION`` and the snapshot writer
already stores floats. See ``docs/plans/phase_4_aggregation_engine/`` for the
design notes.
"""

import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal

import pandas as pd

from src.schemas.gateway import StrategyPerformanceResponse
from src.schemas.strategy import EquityPoint

logger = logging.getLogger(__name__)


def calculate_weighted_return(
    strategies: Sequence[StrategyPerformanceResponse],
    weights: Mapping[str, float],
) -> float:
    """Compute the capital-weighted daily return across strategies.

    Formula (per ROADMAP §4.1):

        ``Σ (daily_pnl_i / total_value_i) × weight_i  /  Σ weights``

    Strategies with ``total_value <= 0`` are excluded from the numerator. The
    denominator is always the full ``sum(weights.values())`` — a strategy
    missing from ``weights`` contributes ``0``.

    Args:
        strategies: Latest performance snapshot for each active strategy.
        weights: Map of ``strategy_id → capital weight``.

    Returns:
        Weighted daily return in fractional form (``0.0148`` ⇒ 1.48 %). Returns
        ``0.0`` when ``sum(weights.values()) <= 0`` (which also covers an empty
        ``weights`` mapping) or when ``strategies`` is empty.

    Example:
        >>> from decimal import Decimal
        >>> from datetime import UTC, datetime
        >>> s = [StrategyPerformanceResponse(
        ...     strategy_id="a", daily_pnl=Decimal("1000"),
        ...     total_value=Decimal("100000"), max_drawdown=Decimal("-0.01"),
        ...     sharpe_ratio=Decimal("1.0"),
        ...     last_updated=datetime(2026, 5, 14, tzinfo=UTC),
        ... )]
        >>> calculate_weighted_return(s, {"a": 1.0})
        0.01
    """
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return 0.0
    weighted = sum(
        (float(s.daily_pnl) / float(s.total_value)) * float(weights.get(s.strategy_id, 0.0))
        for s in strategies
        if s.total_value > 0
    )
    return weighted / total_weight


def merge_equity_curves(
    curves: Mapping[str, Sequence[EquityPoint]],
    weights: Mapping[str, float],
) -> list[EquityPoint]:
    """Merge per-strategy equity curves into a single portfolio curve.

    Algorithm (per ROADMAP §4.3):

    1. Drop any strategy whose curve is empty, weight is ``<= 0``, or first
       value is ``<= 0``.
    2. Normalise each remaining curve to base 100 (divide by its earliest
       value, multiply by 100).
    3. Outer-join on date strings; forward-fill missing dates.
    4. Per row, weighted sum across strategies that *have data on that row*,
       divided by the sum of weights of those strategies. This makes the
       merged curve sensible when a strategy starts trading later than
       another: the portfolio reflects whichever strategies exist on that
       date.

    Args:
        curves: Map of ``strategy_id → equity_curve`` (sequence of
            :class:`EquityPoint`). Empty curves are silently dropped.
        weights: Map of ``strategy_id → capital weight``. Strategies absent
            from ``weights`` or with weight ``<= 0`` are dropped.

    Returns:
        A list of :class:`EquityPoint` covering every date for which at least
        one contributing strategy has data, sorted by date. The values are
        ``Decimal`` rounded to four decimal places (matching
        ``EquityPoint.value``'s ``decimal_places=4`` constraint). Empty list
        if no curve qualifies after filtering.

    Example:
        Two strategies on aligned dates with equal weight produce a curve
        starting at 100 and tracking the average of their two normalised
        movements.
    """
    if not curves:
        return []

    series_by_sid: dict[str, pd.Series] = {}
    weight_by_sid: dict[str, float] = {}

    for sid, curve in curves.items():
        if not curve:
            continue
        weight = float(weights.get(sid, 0.0))
        if weight <= 0:
            continue
        series = pd.Series(
            data=[float(p.value) for p in curve],
            index=[p.date for p in curve],
            dtype=float,
            name=sid,
        ).sort_index()
        first_value = float(series.iloc[0])
        if first_value <= 0:
            continue
        normalised = series / first_value * 100.0
        series_by_sid[sid] = normalised
        weight_by_sid[sid] = weight

    if not series_by_sid:
        return []

    frame = pd.DataFrame(series_by_sid).sort_index().ffill()
    weights_series = pd.Series(weight_by_sid)
    not_na_mask = frame.notna()
    weighted_sum = frame.mul(weights_series, axis=1).fillna(0.0).sum(axis=1)
    row_weight = not_na_mask.mul(weights_series, axis=1).sum(axis=1)
    merged = (weighted_sum / row_weight).dropna()

    return [
        EquityPoint(date=str(date_idx), value=Decimal(f"{value:.4f}"))
        for date_idx, value in merged.items()
    ]


def calculate_combined_drawdown(
    curves: Mapping[str, Sequence[EquityPoint]],
    weights: Mapping[str, float],
) -> float:
    """Compute the max drawdown of the merger of ``curves`` under ``weights``.

    Calls :func:`merge_equity_curves` to produce the portfolio curve, then
    scans it once to find the largest ``value / running_peak - 1``. The
    returned drawdown is negative (matching the sign of ``max_drawdown``
    elsewhere in the codebase) or ``0.0`` if the merged curve never declines.

    Args:
        curves: Map of ``strategy_id → equity_curve``. A strategy with an
            empty curve is dropped (graceful degradation).
        weights: Map of ``strategy_id → capital weight``.

    Returns:
        Max drawdown in fractional form (e.g. ``-0.063`` ⇒ −6.3 %). Returns
        ``0.0`` when the merged curve is empty, monotonically non-decreasing,
        or every running peak is ``<= 0``.

    Example:
        A curve [100, 110, 95, 120] has running peaks [100, 110, 110, 120].
        The drawdown at index 2 is ``95/110 - 1 = -0.1363…`` — the worst.
    """
    merged = merge_equity_curves(curves, weights)
    if not merged:
        return 0.0

    max_drawdown = 0.0
    peak = float(merged[0].value)
    for point in merged:
        value = float(point.value)
        if value > peak:
            peak = value
        if peak <= 0:
            continue
        drawdown = value / peak - 1.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
    return max_drawdown
