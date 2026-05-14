"""Tests for ``src.api.v1.dependencies.verify_api_key``."""

import pytest
from fastapi import HTTPException
from src.api.v1.dependencies import verify_api_key


async def test_verify_api_key_valid() -> None:
    # Matches conftest._TEST_ENV["INTERNAL_API_KEY"]
    await verify_api_key(key="test-internal-api-key")


async def test_verify_api_key_missing() -> None:
    with pytest.raises(HTTPException) as exc:
        await verify_api_key(key=None)
    assert exc.value.status_code == 403


async def test_verify_api_key_wrong() -> None:
    with pytest.raises(HTTPException) as exc:
        await verify_api_key(key="not-the-right-key")
    assert exc.value.status_code == 403


async def test_verify_api_key_empty_string_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        await verify_api_key(key="")
    assert exc.value.status_code == 403
