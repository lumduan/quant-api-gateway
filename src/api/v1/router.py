"""Top-level v1 router.

This router is mounted under ``/api/v1`` by :mod:`src.main`. Later phases
attach sub-routers (ingest, performance, strategies, portfolio) to it as
they are implemented; Phase 1 ships the empty mount point only.
"""

from fastapi import APIRouter

api_router = APIRouter()
