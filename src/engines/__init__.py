"""Engine re-export layer.

Thin re-exports of public symbols from the existing service modules so that
Phase 2 consumers (``quant-openbb/``) have canonical import paths without
reaching into ``src/services/`` internals.
"""

from src.engines.portfolio import (
    SnapshotAggregates,
    build_equity_curve_from_rows,
    calculate_combined_drawdown,
    calculate_weighted_return,
    compute_overall_performance,
    compute_portfolio_equity_curve,
    compute_strategy_performance,
    compute_strategy_performance_range,
    get_benchmark_curve,
    get_latest_report,
    get_report_for_date,
    list_trades,
    maybe_write_snapshot,
    merge_equity_curves,
    persist_report,
    query_latest_snapshot,
    query_snapshot_by_date,
)
from src.engines.registry import (
    clear_registry,
    get_registry,
    load_registry,
    set_registry,
)

__all__ = [
    # portfolio
    "build_equity_curve_from_rows",
    "calculate_combined_drawdown",
    "calculate_weighted_return",
    "compute_overall_performance",
    "compute_portfolio_equity_curve",
    "compute_strategy_performance",
    "compute_strategy_performance_range",
    "get_benchmark_curve",
    "get_latest_report",
    "get_report_for_date",
    "list_trades",
    "maybe_write_snapshot",
    "merge_equity_curves",
    "persist_report",
    "query_latest_snapshot",
    "query_snapshot_by_date",
    "SnapshotAggregates",
    # registry
    "clear_registry",
    "get_registry",
    "load_registry",
    "set_registry",
]
