"""Top-level v1 router.

This router is mounted under ``/api/v1`` by :mod:`src.main`. It re-exports
the ingest, strategies, performance, and portfolio sub-routers.
"""

from fastapi import APIRouter

from src.api.v1 import admin, ingest, performance, portfolio, strategies

api_router = APIRouter()
api_router.include_router(admin.router)
api_router.include_router(ingest.router)
api_router.include_router(strategies.router)
api_router.include_router(performance.router)
api_router.include_router(portfolio.router)
