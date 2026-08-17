"""Wire schemas for assistant event ingestion."""

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

MAX_PAYLOAD_BYTES = 1_000_000


class EventEnvelope(BaseModel):
    """An event delivered by a signed native adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    event_id: str = Field(min_length=1, max_length=200)
    event_type: str = Field(min_length=1, max_length=120)
    native_user_id: str = Field(min_length=1, max_length=200)
    native_chat_id: str | None = Field(default=None, max_length=200)
    native_project_id: str | None = Field(default=None, max_length=200)
    native_folder_id: str | None = Field(default=None, max_length=200)
    native_message_id: str | None = Field(default=None, max_length=200)
    occurred_at: datetime
    payload: dict[str, JsonValue]

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject timestamps without a usable UTC offset."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value

    @field_validator("payload")
    @classmethod
    def require_bounded_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Bound the canonical compact UTF-8 payload representation."""
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
        return value


class EventAccepted(BaseModel):
    """Idempotent event-ingestion result."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=200)
    duplicate: bool
