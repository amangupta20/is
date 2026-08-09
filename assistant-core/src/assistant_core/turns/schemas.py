"""Strict schemas for completed-turn event payloads."""

from hashlib import sha256
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictMessage(BaseModel):
    """Shared canonical fields for one captured native message."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1, max_length=200)
    content: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timestamp: int | None = Field(default=None, ge=0)

    @field_validator("id")
    @classmethod
    def require_nonblank_id(cls, value: str) -> str:
        """Reject IDs that contain no visible characters."""
        if not value.strip():
            raise ValueError("message id must not be blank")
        return value

    @model_validator(mode="after")
    def require_exact_content_digest(self) -> Self:
        """Bind the supplied digest to the exact UTF-8 content bytes."""
        expected = sha256(self.content.encode("utf-8")).hexdigest()
        if self.sha256 != expected:
            raise ValueError("message content digest does not match")
        return self


class CompletedTurnUserMessage(_StrictMessage):
    """The exact user message that began a completed turn."""

    role: Literal["user"]

    @field_validator("content")
    @classmethod
    def require_visible_user_content(cls, value: str) -> str:
        """Reject empty or whitespace-only user input."""
        if not value.strip():
            raise ValueError("user content must not be blank")
        return value


class CompletedTurnAssistantMessage(_StrictMessage):
    """The exact visible assistant response, including a valid empty response."""

    role: Literal["assistant"]


class CompletedTurnPayload(BaseModel):
    """Canonical payload emitted by the Open WebUI outlet filter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: Literal["openwebui_outlet_filter"]
    user_message: CompletedTurnUserMessage
    assistant_message: CompletedTurnAssistantMessage
