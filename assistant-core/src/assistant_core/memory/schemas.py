from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MemoryCategory = str


class ExplicitMemoryCandidate(BaseModel):
    """One quoted, user-authored statement suitable for durable memory storage."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    key: str = Field(
        min_length=3,
        max_length=200,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
    )
    category: str = Field(
        min_length=2,
        max_length=50,
        pattern=r"^[a-z][a-z0-9_-]*$",
        description="Cohesive lowercase category slug (e.g. career, infrastructure, preference, homelab, learning, project)",
    )
    statement: str = Field(min_length=1, max_length=2_000)
    evidence_quote: str = Field(min_length=1, max_length=1_000)
    kind: Literal["explicit", "inferred"] = Field(
        default="explicit",
        description=(
            "explicit for directly stated or agreed facts; inferred for tentative "
            "cross-turn patterns that require corroboration before promotion."
        ),
    )
    valid_from: datetime | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)
    temporal_tag: str | None = Field(default=None, max_length=50)

    @field_validator("statement", "evidence_quote")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        """Reject values that satisfy length constraints but have no visible text."""
        if not value.strip():
            raise ValueError("memory text must not be blank")
        return value
