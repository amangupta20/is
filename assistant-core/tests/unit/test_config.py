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
    assert settings.embedding_base_url is None
    assert settings.embedding_api_key is None
    assert settings.embedding_model is None
    assert settings.embedding_dimension == 1536
    assert settings.embedding_timeout_seconds == 15
    assert settings.log_level == "INFO"
    assert settings.otlp_endpoint is None


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


def test_production_rejects_committed_sample_hmac_secret() -> None:
    """The visible deployment placeholder is never accepted as production entropy."""
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            hmac_secret="REPLACE_WITH_NEW_RANDOM_VALUE_OF_AT_LEAST_32_BYTES",
        )


def test_get_settings_builds_current_environment_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker-facing settings factory reads current ASSISTANT_ values."""
    from assistant_core.config import get_settings

    monkeypatch.setenv("ASSISTANT_LOG_LEVEL", "DEBUG")

    assert get_settings().log_level == "DEBUG"


def test_settings_read_optional_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tracing is enabled only through the explicit assistant setting."""
    monkeypatch.setenv("ASSISTANT_OTLP_ENDPOINT", "http://collector.internal:4318/v1/traces")

    assert Settings().otlp_endpoint == "http://collector.internal:4318/v1/traces"


def test_embedding_dimension_matches_the_fixed_vector_column() -> None:
    """Configuration cannot admit vectors the PostgreSQL schema rejects."""
    with pytest.raises(ValidationError):
        Settings(embedding_dimension=768)
