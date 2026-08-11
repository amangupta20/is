"""Strict extraction boundary for explicit-memory candidates."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MemoryCategory = Literal["fact", "preference", "instruction", "project", "decision"]


class ExplicitMemoryCandidate(BaseModel):
    """One quoted, user-authored statement suitable for durable explicit memory."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    key: str = Field(
        min_length=3,
        max_length=200,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
    )
    category: MemoryCategory
    statement: str = Field(min_length=1, max_length=2_000)
    evidence_quote: str = Field(min_length=1, max_length=1_000)

    @field_validator("statement", "evidence_quote")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        """Reject values that satisfy length constraints but have no visible text."""
        if not value.strip():
            raise ValueError("memory text must not be blank")
        return value
