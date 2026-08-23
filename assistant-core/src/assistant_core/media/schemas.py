"""Pydantic schemas for media analysis, extraction, storage, and search."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MediaSegmentAnalysis(BaseModel):
    """Timestamped segment extraction for media understanding."""

    model_config = ConfigDict(extra="ignore")

    segment_index: int = Field(default=0, ge=0, description="Ordinal index of the segment.")
    start_time_seconds: int = Field(default=0, ge=0, description="Start time in seconds.")
    end_time_seconds: int = Field(default=0, ge=0, description="End time in seconds.")
    label: str | None = Field(default=None, max_length=256, description="Chapter or topic title.")
    content: str = Field(
        ..., description="Detailed textual description/transcript of this segment."
    )


class MediaAnalysisResult(BaseModel):
    """Structured extraction result produced by the multimodal media analyzer."""

    model_config = ConfigDict(extra="ignore")

    url: str = Field(..., max_length=2048, description="Media URL (e.g. YouTube URL).")
    media_type: str = Field(default="youtube", max_length=64, description="Media format/platform.")
    title: str = Field(..., max_length=500, description="Media title.")
    description: str | None = Field(
        default=None, description="Original or synthesized description."
    )
    channel_or_author: str | None = Field(
        default=None, max_length=256, description="Creator or channel name."
    )
    duration_seconds: int | None = Field(
        default=None, ge=0, description="Total media duration in seconds."
    )
    summary: str = Field(..., description="High-density executive synthesis of the media content.")
    key_takeaways: list[str] = Field(
        default_factory=list, description="Key insights, takeaways, or instructions."
    )
    topics: list[str] = Field(default_factory=list, description="Topical tags or domains covered.")
    segments: list[MediaSegmentAnalysis] = Field(
        default_factory=list, description="Chronological timestamped segments."
    )


class MediaSegmentDetail(BaseModel):
    """Detailed media segment record."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    document_id: uuid.UUID
    user_id: uuid.UUID
    segment_index: int
    start_time_seconds: int
    end_time_seconds: int
    label: str | None = None
    content: str
    has_embedding: bool = False
    created_at: datetime


class MediaDocumentDetail(BaseModel):
    """Detailed media document record."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    user_id: uuid.UUID
    url: str
    media_type: str
    title: str
    description: str | None = None
    channel_or_author: str | None = None
    duration_seconds: int | None = None
    summary: str
    key_takeaways: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    total_segments: int = 0
    has_embedding: bool = False
    tombstoned: bool = False
    created_at: datetime
    updated_at: datetime
    segments: list[MediaSegmentDetail] = Field(default_factory=list)


class MediaSearchHit(BaseModel):
    """Search hit for media documents and segments."""

    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    segment_id: uuid.UUID | None = None
    url: str
    media_type: str
    title: str
    channel_or_author: str | None = None
    start_time_seconds: int | None = None
    end_time_seconds: int | None = None
    label: str | None = None
    content: str
    score: float
    match_mode: str = "hybrid"
    created_at: datetime
