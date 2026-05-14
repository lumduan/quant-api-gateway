"""Tests for :mod:`src.config`."""

import pytest
from pydantic import ValidationError
from src.config import Settings, get_settings


def test_settings_loads_from_env() -> None:
    """All required fields populate from environment variables."""
    settings = get_settings()

    assert settings.postgres_dsn.startswith("postgresql://")
    assert settings.mongo_uri.startswith("mongodb://")
    assert settings.redis_url.startswith("redis://")
    assert settings.csm_set_service_url.startswith("http://")
    assert settings.internal_api_key == "test-internal-api-key"
    assert settings.log_level == "INFO"


def test_log_level_defaults_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """``log_level`` falls back to ``INFO`` when the env var is unset."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.log_level == "INFO"


def test_settings_rejects_missing_required_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing a required env var surfaces as ``ValidationError``."""
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    get_settings.cache_clear()

    # ``Settings`` reads env vars at instantiation time. Avoid relying on the
    # local ``.env`` file (developer machines have one) by constructing
    # without ``_env_file``.
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "internal_api_key" in str(excinfo.value).lower()


def test_settings_rejects_empty_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``INTERNAL_API_KEY`` enforces ``min_length=1`` — empty string is rejected."""
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_is_cached() -> None:
    """``get_settings`` returns the same instance on repeated calls."""
    first = get_settings()
    second = get_settings()

    assert first is second
