"""Tests for assistant configuration."""

import pytest
from pydantic import ValidationError

from assistant_core.config import Settings


def test_settings_have_development_defaults() -> None:
    """Development defaults keep a local API runnable."""
    settings = Settings()

    assert settings.environment == "development"
    assert settings.database_url == "postgresql://postgres:postgres@localhost:5432/assistant_core"
    assert settings.hmac_secret == "development-hmac-secret-change-me"
    assert settings.request_clock_skew_seconds == 60
    assert settings.context_timeout_seconds == 1.5
    assert settings.log_level == "INFO"


def test_settings_ignore_unrelated_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only ASSISTANT_-prefixed configuration is considered."""
    monkeypatch.setenv("ENVIRONMENT", "production")

    assert Settings().environment == "development"


def test_production_rejects_the_development_secret() -> None:
    """Production cannot start with its convenience secret."""
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_production_rejects_short_hmac_secret() -> None:
    """Production requires at least 32 encoded secret bytes."""
    with pytest.raises(ValidationError):
        Settings(environment="production", hmac_secret="too-short")
