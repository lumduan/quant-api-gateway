"""Tests for ``src.schemas.registry``."""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from src.schemas.registry import StrategyConfig, StrategyRegistry


def _config(**overrides: object) -> StrategyConfig:
    base: dict[str, object] = {
        "id": "csm-set-01",
        "name": "CSM SET Strategy",
        "type": "EQUITY_MOMENTUM",
        "service_url": "http://quant-csm-set:8001",
        "capital_weight": Decimal("1.0"),
        "active": True,
    }
    base.update(overrides)
    return StrategyConfig.model_validate(base)


def test_strategy_config_valid() -> None:
    cfg = _config()
    assert cfg.id == "csm-set-01"
    assert cfg.type == "EQUITY_MOMENTUM"
    assert cfg.capital_weight == Decimal("1.0")
    assert cfg.active is True


def test_strategy_config_missing_type_rejected() -> None:
    with pytest.raises(ValidationError):
        StrategyConfig.model_validate(
            {
                "id": "csm-set-01",
                "name": "CSM SET Strategy",
                "service_url": "http://quant-csm-set:8001",
                "capital_weight": "1.0",
            }
        )


def test_strategy_config_empty_type_rejected() -> None:
    with pytest.raises(ValidationError):
        _config(type="")


def test_strategy_config_negative_weight_rejected() -> None:
    with pytest.raises(ValidationError):
        _config(capital_weight=Decimal("-0.1"))


def test_strategy_config_id_strips_whitespace() -> None:
    cfg = _config(id="  csm-set-01  ")
    assert cfg.id == "csm-set-01"


def test_strategy_config_active_defaults_true() -> None:
    cfg = StrategyConfig.model_validate(
        {
            "id": "csm-set-01",
            "name": "CSM SET Strategy",
            "type": "EQUITY_MOMENTUM",
            "service_url": "http://quant-csm-set:8001",
            "capital_weight": "1.0",
        }
    )
    assert cfg.active is True


def test_strategy_config_empty_id_rejected() -> None:
    with pytest.raises(ValidationError):
        _config(id="")


def test_strategy_config_is_frozen() -> None:
    cfg = _config()
    with pytest.raises(ValidationError):
        cfg.id = "other"


def test_strategy_registry_active_strategies_filter() -> None:
    registry = StrategyRegistry(
        strategies=[
            _config(id="active-1"),
            _config(id="inactive-1", active=False),
            _config(id="active-2"),
        ]
    )
    active = registry.active_strategies()
    assert {s.id for s in active} == {"active-1", "active-2"}


def test_strategy_registry_by_id_lookup() -> None:
    registry = StrategyRegistry(strategies=[_config(id="found")])
    assert registry.by_id("found") is not None
    assert registry.by_id("missing") is None
