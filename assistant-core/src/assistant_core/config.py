"""Runtime configuration for assistant-core."""

from __future__ import annotations

from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEVELOPMENT_HMAC_SECRET = "development-hmac-secret-change-me"
SAMPLE_HMAC_SECRET = "REPLACE_WITH_NEW_RANDOM_VALUE_OF_AT_LEAST_32_BYTES"


class Settings(BaseSettings):
    """Validated configuration sourced from ASSISTANT_ environment variables."""

    model_config = SettingsConfigDict(env_prefix="ASSISTANT_", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/assistant_core"
    hmac_secret: str = DEVELOPMENT_HMAC_SECRET
    request_clock_skew_seconds: int = 60
    context_timeout_seconds: float = 1.5
    open_webui_url: str = Field(default="http://open-webui:8080", min_length=1, max_length=2_048)
    open_webui_api_key: SecretStr | None = None
    file_indexing_timeout_seconds: float = Field(default=30, ge=1, le=300)
    task_model_base_url: str | None = Field(default=None, min_length=1, max_length=2_048)
    task_model_api_key: SecretStr | None = None
    task_model_model: str | None = Field(default=None, min_length=1, max_length=200)
    task_model_timeout_seconds: float = Field(default=15, ge=1, le=120)
    embedding_base_url: str | None = Field(default=None, min_length=1, max_length=2_048)
    embedding_api_key: SecretStr | None = None
    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_dimension: int = Field(default=1536, ge=1536, le=1536)
    embedding_timeout_seconds: float = Field(default=15, ge=1, le=120)
    log_level: str = "INFO"
    otlp_endpoint: str | None = None

    @field_validator(
        "open_webui_url",
        "task_model_base_url",
        "task_model_model",
        "embedding_base_url",
        "embedding_model",
        "otlp_endpoint",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("open_webui_api_key", "task_model_api_key", "embedding_api_key", mode="before")
    @classmethod
    def _empty_secret_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @model_validator(mode="after")
    def validate_production_hmac_secret(self) -> Self:
        """Reject unsafe HMAC secrets when running in production."""
        if self.environment.lower() == "production" and (
            self.hmac_secret in {DEVELOPMENT_HMAC_SECRET, SAMPLE_HMAC_SECRET}
            or len(self.hmac_secret.encode()) < 32
        ):
            raise ValueError(
                "production requires a non-development HMAC secret of at least 32 bytes"
            )
        return self


def get_settings() -> Settings:
    """Build settings from the current assistant environment."""
    return Settings()
