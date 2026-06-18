"""Top-level v2 router.

Mounts the engine sub-routers under ``/api/v2/engines/``. This router
itself is mounted at ``/api/v2`` by :mod:`src.main`.
"""

from fastapi import APIRouter

from src.api.v2.engines import (
    backtest,
    catalog,
    execution,
    market_data,
    orderbook,
    portfolio,
    signals,
)

api_router = APIRouter()

api_router.include_router(portfolio.router, prefix="/engines/portfolio")
api_router.include_router(backtest.router, prefix="/engines/backtest")
api_router.include_router(market_data.router, prefix="/engines/market-data")
api_router.include_router(execution.router, prefix="/engines/execution")
api_router.include_router(orderbook.router, prefix="/engines/orderbook")
api_router.include_router(signals.router, prefix="/engines/signals")
api_router.include_router(catalog.router, prefix="/engines")
