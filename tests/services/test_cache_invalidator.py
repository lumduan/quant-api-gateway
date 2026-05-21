"""Unit tests for ``src.services.cache_invalidator``."""

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from src.services import cache_invalidator as ci
from src.services.errors import CacheError


@pytest.fixture
def mock_invalidate(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace ``src.services.cache.invalidate_key`` with a mock."""
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.services.cache_invalidator.invalidate_key", mock)
    return mock


@pytest.fixture
def mock_invalidate_pattern(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace ``src.services.cache.invalidate_pattern`` with a mock."""
    mock = AsyncMock(return_value=5)
    monkeypatch.setattr("src.services.cache_invalidator.invalidate_pattern", mock)
    return mock


# ---- invalidate_overall_cache --------------------------------------------------


async def test_invalidate_overall_cache_deletes_correct_key(mock_invalidate: AsyncMock) -> None:
    await ci.invalidate_overall_cache()
    mock_invalidate.assert_awaited_once_with("overall_performance")


async def test_invalidate_overall_cache_logs_on_failure(
    mock_invalidate: AsyncMock, caplog: Any
) -> None:
    caplog.set_level(logging.ERROR)
    mock_invalidate.side_effect = RedisConnectionError("down")
    # Must NOT raise
    await ci.invalidate_overall_cache()
    assert any("failed to invalidate overall_performance" in m for m in caplog.messages)


async def test_invalidate_overall_cache_never_raises(mock_invalidate: AsyncMock) -> None:
    mock_invalidate.side_effect = Exception("unexpected")
    # Must not propagate
    await ci.invalidate_overall_cache()


# ---- invalidate_strategy_cache -------------------------------------------------


async def test_invalidate_strategy_cache_deletes_correct_key(mock_invalidate: AsyncMock) -> None:
    await ci.invalidate_strategy_cache("csm-set-01")
    mock_invalidate.assert_awaited_once_with("strategy:csm-set-01:performance")


async def test_invalidate_strategy_cache_logs_on_failure(
    mock_invalidate: AsyncMock, caplog: Any
) -> None:
    caplog.set_level(logging.ERROR)
    mock_invalidate.side_effect = RedisConnectionError("down")
    await ci.invalidate_strategy_cache("csm-set-01")
    assert any("failed to invalidate strategy:csm-set-01:performance" in m for m in caplog.messages)


async def test_invalidate_strategy_cache_never_raises(mock_invalidate: AsyncMock) -> None:
    mock_invalidate.side_effect = Exception("unexpected")
    await ci.invalidate_strategy_cache("any-id")


# ---- flush_all -----------------------------------------------------------------


async def test_flush_all_uses_correct_pattern(mock_invalidate_pattern: AsyncMock) -> None:
    count = await ci.flush_all()
    assert count == 5
    mock_invalidate_pattern.assert_awaited_once_with("gateway:*")


async def test_flush_all_propagates_errors(mock_invalidate_pattern: AsyncMock) -> None:
    mock_invalidate_pattern.side_effect = CacheError("redis down")
    with pytest.raises(CacheError):
        await ci.flush_all()


# ---- constant correctness ------------------------------------------------------


def test_key_constants() -> None:
    assert ci.OVERALL_PERFORMANCE_KEY == "overall_performance"
    assert ci.STRATEGY_PERFORMANCE_PREFIX == "strategy:"
    assert ci.STRATEGY_PERFORMANCE_SUFFIX == ":performance"
    assert ci.GATEWAY_CACHE_PATTERN == "gateway:*"


# ---- per-strategy report-bundle invalidators (feature-strategies-report-metrics)


async def test_invalidate_strategy_report_keys_pattern(
    mock_invalidate_pattern: AsyncMock,
) -> None:
    await ci.invalidate_strategy_report_keys("csm-set-01")
    mock_invalidate_pattern.assert_awaited_once_with("gateway:strategy:csm-set-01:report:*")


async def test_invalidate_strategy_report_keys_never_raises(
    mock_invalidate_pattern: AsyncMock, caplog: Any
) -> None:
    caplog.set_level(logging.ERROR)
    mock_invalidate_pattern.side_effect = CacheError("redis down")
    # Must NOT raise.
    await ci.invalidate_strategy_report_keys("csm-set-01")
    assert any("failed to invalidate pattern" in m for m in caplog.messages)


async def test_invalidate_strategy_trade_keys_pattern(
    mock_invalidate_pattern: AsyncMock,
) -> None:
    await ci.invalidate_strategy_trade_keys("csm-set-01")
    mock_invalidate_pattern.assert_awaited_once_with("gateway:strategy:csm-set-01:trades:*")


async def test_invalidate_strategy_trade_keys_never_raises(
    mock_invalidate_pattern: AsyncMock,
) -> None:
    mock_invalidate_pattern.side_effect = RedisConnectionError("down")
    await ci.invalidate_strategy_trade_keys("csm-set-01")


async def test_invalidate_strategy_benchmark_keys_pattern(
    mock_invalidate_pattern: AsyncMock,
) -> None:
    await ci.invalidate_strategy_benchmark_keys("csm-set-01")
    mock_invalidate_pattern.assert_awaited_once_with("gateway:strategy:csm-set-01:benchmark:*")


async def test_invalidate_strategy_benchmark_keys_never_raises(
    mock_invalidate_pattern: AsyncMock,
) -> None:
    mock_invalidate_pattern.side_effect = Exception("unexpected")
    await ci.invalidate_strategy_benchmark_keys("csm-set-01")


async def test_invalidate_strategy_report_bundle_runs_all_three(
    mock_invalidate_pattern: AsyncMock,
) -> None:
    """The bundle helper invokes every pattern in one await."""
    await ci.invalidate_strategy_report_bundle("csm-set-01")

    patterns = [c.args[0] for c in mock_invalidate_pattern.await_args_list]
    assert patterns == [
        "gateway:strategy:csm-set-01:report:*",
        "gateway:strategy:csm-set-01:trades:*",
        "gateway:strategy:csm-set-01:benchmark:*",
    ]
