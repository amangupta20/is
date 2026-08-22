"""Pydantic schemas for topic episode extraction, search results, and API responses."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TopicEpisodeExtraction(BaseModel):
    """One extracted topic episode from a session turn sequence."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., max_length=256, description="Clear, descriptive episode title.")
    topic_category: str = Field(
        default="general",
        max_length=64,
        description="Domain category e.g. architecture, debugging, configuration, research, etc.",
    )
    summary: str = Field(
        ...,
        description="Rich, multi-sentence executive synthesis of what was discussed and established.",
    )
    decisions_made: list[str] = Field(
        default_factory=list,
        description="Explicit technical, architectural, or procedural decisions agreed in this episode.",
    )
    open_loops: list[str] = Field(
        default_factory=list,
        description="Unresolved questions, pending tasks, or future milestones mentioned.",
    )
    key_entities: list[str] = Field(
        default_factory=list,
        description="Mentioned technologies, libraries, services, files, or key concepts.",
    )
    start_message_id: str = Field(
        ...,
        description="The native user or assistant message ID starting this episode topic.",
    )
    end_message_id: str = Field(
        ...,
        description="The native user or assistant message ID concluding this episode topic.",
    )


class TopicEpisodeHit(BaseModel):
    """Ranked topic episode search result for hierarchical retrieval."""

    model_config = ConfigDict(extra="forbid")

    episode_id: uuid.UUID
    native_chat_id: str
    native_project_id: str | None = None
    native_folder_id: str | None = None
    title: str
    topic_category: str
    summary: str
    decisions_made: list[str] = Field(default_factory=list)
    open_loops: list[str] = Field(default_factory=list)
    key_entities: list[str] = Field(default_factory=list)
    start_message_id: str
    end_message_id: str
    turn_count: int
    score: float
    match_mode: str = "hybrid"
    created_at: datetime


class TopicEpisodeDetail(BaseModel):
    """Full topic episode record for inspection and administrative management."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    user_id: uuid.UUID
    native_chat_id: str
    native_project_id: str | None = None
    native_folder_id: str | None = None
    title: str
    topic_category: str
    summary: str
    decisions_made: list[str]
    open_loops: list[str]
    key_entities: list[str]
    start_message_id: str
    end_message_id: str
    turn_count: int
    has_embedding: bool
    tombstoned: bool
    created_at: datetime
    updated_at: datetime
