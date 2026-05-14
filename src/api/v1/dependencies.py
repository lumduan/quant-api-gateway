"""Shared FastAPI dependencies for the v1 API."""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from src.config import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str | None = Depends(_api_key_header)) -> None:
    """Reject requests that do not present a matching ``X-API-Key`` header.

    Compares the header against :attr:`Settings.internal_api_key` in constant
    time so an attacker cannot probe the secret with timing measurements.
    A missing header is treated as a wrong key — both paths produce ``403``.
    """
    expected = get_settings().internal_api_key
    provided = key or ""
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )
