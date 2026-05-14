"""Load and access the strategy registry (``strategies.json``).

The registry is loaded eagerly at application startup by :mod:`src.main`'s
lifespan and stored in a module-global. Access at runtime through
:func:`get_registry`. Tests inject a registry via :func:`set_registry`.
"""

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from src.schemas.registry import StrategyRegistry
from src.services.errors import StrategyRegistryLoadError

logger = logging.getLogger(__name__)

_registry: StrategyRegistry | None = None


def load_registry(path: Path) -> StrategyRegistry:
    """Read ``strategies.json`` from ``path`` and return a parsed registry.

    Args:
        path: Filesystem path to the registry JSON file.

    Returns:
        The validated :class:`StrategyRegistry`.

    Raises:
        StrategyRegistryLoadError: If the file is missing, malformed JSON, or
            fails Pydantic validation.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise StrategyRegistryLoadError(f"strategy registry not found: {path}") from exc
    except OSError as exc:
        raise StrategyRegistryLoadError(f"cannot read strategy registry {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StrategyRegistryLoadError(
            f"strategy registry {path} is not valid JSON: {exc}"
        ) from exc

    try:
        return StrategyRegistry.model_validate(data)
    except ValidationError as exc:
        raise StrategyRegistryLoadError(
            f"strategy registry {path} failed validation: {exc}"
        ) from exc


def set_registry(registry: StrategyRegistry) -> None:
    """Populate the module-global registry.

    Called once by the application lifespan and by tests that need a known
    registry without touching the filesystem.
    """
    global _registry
    _registry = registry
    logger.info("strategy registry loaded with %d entries", len(registry.strategies))


def get_registry() -> StrategyRegistry:
    """Return the currently-loaded registry.

    Raises:
        StrategyRegistryLoadError: If :func:`set_registry` has not been called.
    """
    if _registry is None:
        raise StrategyRegistryLoadError("strategy registry has not been loaded")
    return _registry


def clear_registry() -> None:
    """Drop the module-global registry. Called by the lifespan on shutdown."""
    global _registry
    _registry = None
