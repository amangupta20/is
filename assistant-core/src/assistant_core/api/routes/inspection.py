"""Signed read-only inspection for indexed conversations and jobs."""

from datetime import datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.conversation.repository import get_conversation_stats, get_recent_references
from assistant_core.files.repository import get_file_stats
from assistant_core.identity.models import UserIdentity

router = APIRouter(prefix="/v1/inspection", tags=["inspection"])
LOGGER = structlog.get_logger("assistant_core.inspection")
class InspectionRecentRequest(BaseModel):
    """Owner-scoped request for the most recent references."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    native_chat_id: str | None = Field(default=None, max_length=200)
    native_message_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=5, ge=1, le=10)


class RecentReferenceItem(BaseModel):
    """Metadata-only reference without content."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str
    native_chat_id: str
    native_message_id: str
    role: Literal["user", "assistant"]
    chunk_ordinal: int
    created_at: datetime
    tombstoned_at: datetime | None


class InspectionRecentResponse(BaseModel):
    """Bounded recent references."""

    model_config = ConfigDict(extra="forbid")

    results: list[RecentReferenceItem]


class InspectionStatsRequest(BaseModel):
    """Owner-scoped request for aggregate index counters."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    native_chat_id: str | None = Field(default=None, max_length=200)
    native_message_id: str | None = Field(default=None, max_length=200)


class InspectionStatsResponse(BaseModel):
    """Metadata-only index and queue counters."""

    model_config = ConfigDict(extra="forbid")

    total_segments: int = Field(ge=0)
    embedded_segments: int = Field(ge=0)
    lexical_segments: int = Field(ge=0)
    total_references: int = Field(ge=0)
    active_references: int = Field(ge=0)
    tombstoned_references: int = Field(ge=0)
    total_file_segments: int = Field(default=0, ge=0)
    embedded_file_segments: int = Field(default=0, ge=0)
    lexical_file_segments: int = Field(default=0, ge=0)
    total_file_references: int = Field(default=0, ge=0)
    active_file_references: int = Field(default=0, ge=0)
    tombstoned_file_references: int = Field(default=0, ge=0)
    queued_jobs: int = Field(ge=0)
    dead_jobs: int = Field(ge=0)
    last_indexed_at: datetime | None


@router.post(
    "/recent",
    dependencies=[Depends(require_adapter_signature)],
    response_model=InspectionRecentResponse,
)
async def inspection_recent(
    body: InspectionRecentRequest, request: Request
) -> InspectionRecentResponse:
    """Return the caller's most recent references without content."""
    async with request.app.state.session_factory() as session:
        user_id = (
            await session.execute(
                select(UserIdentity.id).where(UserIdentity.native_user_id == body.native_user_id)
            )
        ).scalar_one_or_none()
        if user_id is None:
            LOGGER.info("inspection_recent_completed", user_found=False, limit=body.limit)
            return InspectionRecentResponse(results=[])
        refs = await get_recent_references(session, user_id, body.limit)
        items = [
            RecentReferenceItem(
                reference_id=str(r.id),
                native_chat_id=r.native_chat_id,
                native_message_id=r.native_message_id,
                role=r.role,  # type: ignore[arg-type]
                chunk_ordinal=r.chunk_ordinal,
                created_at=r.created_at,
                tombstoned_at=r.tombstoned_at,
            )
            for r in refs
        ]
        LOGGER.info(
            "inspection_recent_completed",
            user_found=True,
            limit=body.limit,
            returned=len(items),
        )
        return InspectionRecentResponse(results=items)


@router.post(
    "/stats",
    dependencies=[Depends(require_adapter_signature)],
    response_model=InspectionStatsResponse,
)
async def inspection_stats(
    body: InspectionStatsRequest, request: Request
) -> InspectionStatsResponse:
    """Return per-user conversation counts and global queue counters."""
    async with request.app.state.session_factory() as session:
        user_id = (
            await session.execute(
                select(UserIdentity.id).where(UserIdentity.native_user_id == body.native_user_id)
            )
        ).scalar_one_or_none()
        if user_id is None:
            # Return global queue counts only; conversation counts are zero
            from sqlalchemy import func

            from assistant_core.jobs.models import Job

            queued = (
                await session.execute(select(func.count()).select_from(Job).where(Job.status == "queued"))
            ).scalar_one()
            dead = (
                await session.execute(select(func.count()).select_from(Job).where(Job.status == "dead"))
            ).scalar_one()
            LOGGER.info("inspection_stats_completed", user_found=False)
            return InspectionStatsResponse(
                total_segments=0,
                embedded_segments=0,
                lexical_segments=0,
                total_references=0,
                active_references=0,
                tombstoned_references=0,
                total_file_segments=0,
                embedded_file_segments=0,
                lexical_file_segments=0,
                total_file_references=0,
                active_file_references=0,
                tombstoned_file_references=0,
                queued_jobs=queued,
                dead_jobs=dead,
                last_indexed_at=None,
            )
        stats = await get_conversation_stats(session, user_id)
        file_stats = await get_file_stats(session, user_id)
        LOGGER.info(
            "inspection_stats_completed",
            user_found=True,
            total_segments=stats["total_segments"],
            embedded_segments=stats["embedded_segments"],
            total_references=stats["total_references"],
            total_file_segments=file_stats["total_segments"],
        )
        return InspectionStatsResponse(
            **stats,  # type: ignore[arg-type]
            total_file_segments=file_stats["total_segments"],  # type: ignore[arg-type]
            embedded_file_segments=file_stats["embedded_segments"],  # type: ignore[arg-type]
            lexical_file_segments=file_stats["lexical_segments"],  # type: ignore[arg-type]
            total_file_references=file_stats["total_references"],  # type: ignore[arg-type]
            active_file_references=file_stats["active_references"],  # type: ignore[arg-type]
            tombstoned_file_references=file_stats["tombstoned_references"],  # type: ignore[arg-type]
        )  # type: ignore[arg-type]
