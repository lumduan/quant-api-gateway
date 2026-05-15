"""Unit tests for ``src.services.cache`` — all Redis calls mocked."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from src.schemas.gateway import OverallPerformanceResponse, StrategyPerformanceResponse
from src.services import cache
from src.services.errors import CacheError


@pytest.fixture
def mock_redis(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace ``src.db.redis_client.get_redis`` with a mock Redis."""
    redis = AsyncMock(name="mock-redis")
    redis.get = AsyncMock(name="redis-get", return_value=None)
    redis.setex = AsyncMock(name="redis-setex", return_value=None)
    redis.delete = AsyncMock(name="redis-delete", return_value=None)
    redis.scan = AsyncMock(name="redis-scan", return_value=(0, []))

    async def _get_redis() -> AsyncMock:
        return redis

    monkeypatch.setattr("src.services.cache.get_redis", _get_redis)
    return redis


# ---- get_cached -----------------------------------------------------------------


async def test_get_cached_miss_returns_none(mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = None
    result = await cache.get_cached("key", StrategyPerformanceResponse)
    assert result is None


async def test_get_cached_hit_returns_model(mock_redis: AsyncMock) -> None:
    model = StrategyPerformanceResponse(
        strategy_id="s1",
        daily_pnl=Decimal("1500"),
        total_value=Decimal("100000"),
        max_drawdown=Decimal("-0.05"),
        sharpe_ratio=Decimal("1.5"),
        last_updated=datetime(2026, 5, 15, tzinfo=UTC),
    )
    mock_redis.get.return_value = model.model_dump_json()
    result = await cache.get_cached("strategy:s1:performance", StrategyPerformanceResponse)
    assert result is not None
    assert result.strategy_id == "s1"
    assert result.daily_pnl == Decimal("1500")


async def test_get_cached_corrupt_json_returns_none(mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = "{not valid json"
    result = await cache.get_cached("key", StrategyPerformanceResponse)
    assert result is None


async def test_get_cached_validation_failure_returns_none(mock_redis: AsyncMock) -> None:
    """JSON that parses but fails Pydantic validation → None (graceful)."""
    mock_redis.get.return_value = json.dumps({"strategy_id": "s1"})  # missing required fields
    result = await cache.get_cached("key", StrategyPerformanceResponse)
    assert result is None


async def test_get_cached_raises_cache_error_on_redis_failure(mock_redis: AsyncMock) -> None:
    mock_redis.get.side_effect = RedisConnectionError("connection refused")
    with pytest.raises(CacheError, match="redis GET failed"):
        await cache.get_cached("key", StrategyPerformanceResponse)


# ---- set_cached -----------------------------------------------------------------


async def test_set_cached_uses_setex(mock_redis: AsyncMock) -> None:
    model = StrategyPerformanceResponse(
        strategy_id="s1",
        daily_pnl=Decimal("1000"),
        total_value=Decimal("50000"),
        max_drawdown=Decimal("-0.03"),
        sharpe_ratio=Decimal("1.2"),
        last_updated=datetime(2026, 5, 15, tzinfo=UTC),
    )
    await cache.set_cached("strategy:s1:performance", model, ttl=300)
    mock_redis.setex.assert_awaited_once_with(
        "strategy:s1:performance", 300, model.model_dump_json()
    )


async def test_set_cached_raises_cache_error_on_redis_failure(mock_redis: AsyncMock) -> None:
    mock_redis.setex.side_effect = RedisConnectionError("connection refused")
    model = StrategyPerformanceResponse(
        strategy_id="s1",
        daily_pnl=Decimal("1000"),
        total_value=Decimal("50000"),
        max_drawdown=Decimal("-0.03"),
        sharpe_ratio=Decimal("1.2"),
        last_updated=datetime(2026, 5, 15, tzinfo=UTC),
    )
    with pytest.raises(CacheError, match="redis SETEX failed"):
        await cache.set_cached("key", model, ttl=300)


# ---- invalidate_key -------------------------------------------------------------


async def test_invalidate_key_deletes(mock_redis: AsyncMock) -> None:
    await cache.invalidate_key("my-key")
    mock_redis.delete.assert_awaited_once_with("my-key")


async def test_invalidate_key_raises_cache_error_on_redis_failure(mock_redis: AsyncMock) -> None:
    mock_redis.delete.side_effect = RedisConnectionError("connection refused")
    with pytest.raises(CacheError, match="redis DELETE failed"):
        await cache.invalidate_key("key")


# ---- invalidate_pattern ---------------------------------------------------------


async def test_invalidate_pattern_scans_and_deletes(mock_redis: AsyncMock) -> None:
    mock_redis.scan.side_effect = [
        (3, ["gateway:k1", "gateway:k2"]),
        (7, ["gateway:k3"]),
        (0, []),
    ]
    deleted = await cache.invalidate_pattern("gateway:*")
    assert deleted == 3
    assert mock_redis.delete.await_count == 2
    mock_redis.delete.assert_any_await("gateway:k1", "gateway:k2")
    mock_redis.delete.assert_any_await("gateway:k3")


async def test_invalidate_pattern_no_matches(mock_redis: AsyncMock) -> None:
    mock_redis.scan.return_value = (0, [])
    deleted = await cache.invalidate_pattern("nonexistent:*")
    assert deleted == 0
    mock_redis.delete.assert_not_awaited()


async def test_invalidate_pattern_raises_cache_error_on_redis_failure(
    mock_redis: AsyncMock,
) -> None:
    mock_redis.scan.side_effect = RedisConnectionError("connection refused")
    with pytest.raises(CacheError, match="redis SCAN/DELETE failed"):
        await cache.invalidate_pattern("gateway:*")


# ---- round-trip tests -----------------------------------------------------------


async def test_strategy_performance_response_round_trip(mock_redis: AsyncMock) -> None:
    original = StrategyPerformanceResponse(
        strategy_id="csm-set-01",
        daily_pnl=Decimal("15000.50"),
        total_value=Decimal("1050000.00"),
        max_drawdown=Decimal("-0.063"),
        sharpe_ratio=Decimal("1.85"),
        last_updated=datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
    )
    mock_redis.get.return_value = original.model_dump_json()
    restored = await cache.get_cached("k", StrategyPerformanceResponse)
    assert restored is not None
    assert restored.strategy_id == original.strategy_id
    assert restored.daily_pnl == original.daily_pnl
    assert restored.total_value == original.total_value
    assert restored.max_drawdown == original.max_drawdown
    assert restored.sharpe_ratio == original.sharpe_ratio
    assert restored.last_updated == original.last_updated


async def test_overall_performance_response_round_trip(mock_redis: AsyncMock) -> None:
    nested = StrategyPerformanceResponse(
        strategy_id="csm-set-01",
        daily_pnl=Decimal("15000.50"),
        total_value=Decimal("1050000.00"),
        max_drawdown=Decimal("-0.063"),
        sharpe_ratio=Decimal("1.85"),
        last_updated=datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
    )
    original = OverallPerformanceResponse(
        total_portfolio_value=Decimal("1050000.00"),
        weighted_daily_return=Decimal("0.014800"),
        combined_max_drawdown=Decimal("-0.0630"),
        active_strategies=1,
        allocation={"csm-set-01": Decimal("1.0")},
        strategies=[nested],
        computed_at=datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC),
    )
    mock_redis.get.return_value = original.model_dump_json()
    restored = await cache.get_cached("overall_performance", OverallPerformanceResponse)
    assert restored is not None
    assert restored.total_portfolio_value == original.total_portfolio_value
    assert restored.weighted_daily_return == original.weighted_daily_return
    assert restored.combined_max_drawdown == original.combined_max_drawdown
    assert restored.active_strategies == original.active_strategies
    assert restored.allocation == original.allocation
    assert len(restored.strategies) == 1
    assert restored.strategies[0].strategy_id == "csm-set-01"
    assert restored.computed_at == original.computed_at


# ---- get_cached type parameter --------------------------------------------------


async def test_get_cached_returns_none_for_null_redis_response(mock_redis: AsyncMock) -> None:
    """Explicit None from Redis GET is a miss."""
    mock_redis.get.return_value = None
    result = await cache.get_cached("any-key", StrategyPerformanceResponse)
    assert result is None


async def test_get_cached_logs_warning_on_corrupt_json(mock_redis: AsyncMock, caplog: Any) -> None:
    import logging

    caplog.set_level(logging.WARNING)
    mock_redis.get.return_value = "{{{bad"
    result = await cache.get_cached("k", StrategyPerformanceResponse)
    assert result is None
    assert any("corrupt JSON" in m for m in caplog.messages)
