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
                "type": "EQUITY_MOMENTUM",
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
                    "type": "EQUITY_MOMENTUM",
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


def test_production_strategies_json_parses_with_expected_entries() -> None:
    """The repo's production ``strategies.json`` must always parse cleanly
    and contain the registered strategies. Guards against typos / missing
    fields when adding a new strategy.

    Until 2026-10-14 this asserted ``tfex-s50-multi-tf-swing`` was registered.
    That strategy was deregistered in f2f81c2 (2026-09-04, decommissioned; its
    repo is archived), and this test failed on ``main`` from then on. The
    per-entry checks now cover ``cash-and-carry-set-tfex``, the registry's
    paper-only entry.
    """
    repo_root = Path(__file__).resolve().parents[2]
    reg = registry_mod.load_registry(repo_root / "strategies.json")
    ids = {s.id for s in reg.strategies}
    assert "csm-set" in ids
    assert "cash-and-carry-set-tfex" in ids
    carry = reg.by_id("cash-and-carry-set-tfex")
    assert carry is not None
    assert carry.type == "ARBITRAGE_BASIS"
    assert carry.service_url == "http://quant-cash-and-carry-set-tfex:8000"
    assert carry.active is False  # paper-only, excluded from portfolio aggregation
    # Deregistered on purpose (f2f81c2): its container is gone, so re-adding the
    # entry (a revert, say) would register a service nothing answers for.
    assert "tfex-s50-multi-tf-swing" not in ids
