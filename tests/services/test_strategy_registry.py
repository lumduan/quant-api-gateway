"""Tests for ``src.services.strategy_registry``."""

import json
from pathlib import Path

import pytest
from src.schemas.registry import StrategyConfig, StrategyRegistry
from src.services import strategy_registry as registry_mod
from src.services.errors import StrategyRegistryLoadError


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    """Reset module-global registry state before every test."""
    registry_mod._registry = None


def _write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_registry_happy_path(tmp_path: Path) -> None:
    payload = {
        "strategies": [
            {
                "id": "csm-set-01",
                "name": "CSM SET Strategy",
                "service_url": "http://quant-csm-set:8001",
                "capital_weight": "1.0",
                "active": True,
            }
        ]
    }
    path = _write_json(tmp_path / "strategies.json", payload)
    reg = registry_mod.load_registry(path)
    assert isinstance(reg, StrategyRegistry)
    assert len(reg.strategies) == 1
    assert reg.strategies[0].id == "csm-set-01"


def test_load_registry_missing_file(tmp_path: Path) -> None:
    with pytest.raises(StrategyRegistryLoadError, match="not found"):
        registry_mod.load_registry(tmp_path / "does-not-exist.json")


def test_load_registry_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(StrategyRegistryLoadError, match="not valid JSON"):
        registry_mod.load_registry(path)


def test_load_registry_validation_error(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "bad.json",
        {"strategies": [{"id": "", "name": "x", "service_url": "u", "capital_weight": "1"}]},
    )
    with pytest.raises(StrategyRegistryLoadError, match="failed validation"):
        registry_mod.load_registry(path)


def test_load_registry_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-FileNotFoundError OSError still surfaces as ``StrategyRegistryLoadError``."""
    target = tmp_path / "perm-denied.json"
    target.write_text("{}", encoding="utf-8")

    def _boom(self: Path, encoding: str | None = None) -> str:  # noqa: ARG001
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    with pytest.raises(StrategyRegistryLoadError, match="cannot read"):
        registry_mod.load_registry(target)


def test_get_registry_unset_raises() -> None:
    with pytest.raises(StrategyRegistryLoadError, match="has not been loaded"):
        registry_mod.get_registry()


def test_set_and_clear_roundtrip() -> None:
    reg = StrategyRegistry(
        strategies=[
            StrategyConfig.model_validate(
                {
                    "id": "x",
                    "name": "x",
                    "service_url": "http://x",
                    "capital_weight": "0.5",
                }
            )
        ]
    )
    registry_mod.set_registry(reg)
    assert registry_mod.get_registry() is reg
    registry_mod.clear_registry()
    with pytest.raises(StrategyRegistryLoadError):
        registry_mod.get_registry()
