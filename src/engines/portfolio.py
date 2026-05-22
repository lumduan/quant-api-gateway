"""Portfolio engine re-exports.

Re-exports all public symbols from the portfolio-related service modules:
aggregator, portfolio, snapshot_writer, performance, and strategy_report_service.
No new logic — pure delegation to the existing service layer.
"""

from src.services.aggregator import (
    calculate_combined_drawdown,
    calculate_weighted_return,
    merge_equity_curves,
)
from src.services.performance import (
    compute_overall_performance,
    compute_strategy_performance,
    compute_strategy_performance_range,
)
from src.services.portfolio import (
    compute_portfolio_equity_curve,
    query_latest_snapshot,
    query_snapshot_by_date,
)
from src.services.snapshot_writer import (
    SnapshotAggregates,
    build_equity_curve_from_rows,
    maybe_write_snapshot,
)
from src.services.strategy_report_service import (
    get_benchmark_curve,
    get_latest_report,
    get_report_for_date,
    list_trades,
    persist_report,
)

__all__ = [
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
]
