import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.orm import selectinload

from assistant_core.artifacts.models import Artifact, ArtifactVersion, OnlyOfficeSession
from assistant_core.artifacts.onlyoffice import OnlyOfficeManager
from assistant_core.artifacts.repository import (
    ArtifactRepository,
    compute_unified_diff,
    extract_artifact_text,
)
from assistant_core.artifacts.schemas import RevertArtifactRequest
from assistant_core.auth.admin import (
    DEFAULT_SESSION_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    create_admin_session_token,
    require_admin_session,
    verify_admin_credentials,
)
from assistant_core.conversation.embedder import (
    ConversationEmbeddingError,
    EmbeddingConfigurationError,
    get_conversation_embedder,
)
from assistant_core.conversation.models import ConversationReference, ConversationSegment
from assistant_core.conversation.repository import search_conversation_context
from assistant_core.episodes.extractor import TurnSummaryInput
from assistant_core.episodes.models import TopicEpisode
from assistant_core.episodes.repository import (
    create_topic_episode,
    delete_topic_episode,
    detect_inactive_chats_for_compilation,
    get_topic_episode,
    get_uncompiled_turns_for_chat,
    list_topic_episodes,
    search_topic_episodes,
)
from assistant_core.events.models import EventInbox
from assistant_core.files.models import (
    FileDocument,
    FileReference,
    FileSegment,
    KBReconciliationRun,
)
from assistant_core.files.repository import (
    reconcile_and_log_kb_documents,
    search_file_passages,
    tombstone_file_references,
)
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.jobs.worker import (
    TaskModelConfigurationError,
    get_episode_extractor,
    get_media_analyzer,
    get_memory_consolidator,
)
from assistant_core.media.models import MediaDocument, MediaSegment
from assistant_core.media.repository import (
    delete_media_document,
    search_media_segments,
    store_media_analysis,
    tombstone_media_document,
)
from assistant_core.memory.consolidator import MemoryConsolidationError
from assistant_core.memory.models import (
    ChatProfileSnapshot,
    ConsolidationRun,
    MemoryEvidence,
    MemoryRecord,
)
from assistant_core.memory.profile import get_or_create_profile
from assistant_core.memory.repository import (
    _memory_snapshot,
    consolidate_user_memories,
    list_memory_change_logs,
    log_memory_change,
    revert_consolidation_item,
    revert_memory_change_log,
    search_explicit_memory,
)
from assistant_core.turns.models import CompletedTurn

LOGGER = structlog.get_logger("assistant_core.admin")

router = APIRouter(prefix="/v1/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------


class AdminLoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=500)


class ConsolidateMemoriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    native_user_id: str | None = None


class PlaygroundSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    native_user_id: str = Field(default="user-1", min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=50)
    source_type: Literal["all", "memories", "files", "conversations", "episodes", "media"] = "all"

class CompileEpisodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    native_chat_id: str | None = None
    native_user_id: str | None = "user-1"


class AdminLoginResponse(BaseModel):
    status: str
    token: str


class CreateMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    native_user_id: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="preference", min_length=1, max_length=100)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence_quote: str | None = Field(default=None, max_length=2000)
    valid_from: datetime | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)
    temporal_tag: str | None = Field(default=None, max_length=50)


class UpdateMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str | None = Field(default=None, min_length=1, max_length=2000)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    state: Literal["active", "tombstoned"] | None = None
    valid_from: datetime | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)
    temporal_tag: str | None = Field(default=None, max_length=50)


class BatchDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


class BatchDeleteFilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    native_file_ids: list[str] = Field(min_length=1, max_length=1000)


class PurgeSystemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str
    scope: Literal["all", "memories", "files", "conversations", "jobs", "artifacts", "episodes", "media"] = "all"


class IndexMediaUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    native_user_id: str = Field(default="user-1", min_length=1, max_length=200)
    media_type: str = Field(default="youtube", min_length=1, max_length=64)

# ---------------------------------------------------------------------------
# Authentication Routes
# ---------------------------------------------------------------------------


@router.post("/auth/login", response_model=AdminLoginResponse)
async def admin_login(
    body: AdminLoginRequest, request: Request, response: Response
) -> AdminLoginResponse:
    """Validate admin token and issue session cookie."""
    settings = request.app.state.settings
    if not verify_admin_credentials(body.token, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin token",
        )

    signing_key = (
        settings.admin_token.get_secret_value()
        if settings.admin_token is not None
        else settings.hmac_secret
    )
    session_token = create_admin_session_token(signing_key)

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=DEFAULT_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.environment.lower() == "production",
    )
    return AdminLoginResponse(status="authenticated", token=session_token)


@router.post("/auth/logout")
async def admin_logout(response: Response) -> dict[str, str]:
    """Clear admin session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return {"status": "logged_out"}


@router.get("/auth/check", dependencies=[Depends(require_admin_session)])
async def admin_auth_check() -> dict[str, bool]:
    """Verify active admin session."""
    return {"authenticated": True}


# ---------------------------------------------------------------------------
# Overview & Telemetry
# ---------------------------------------------------------------------------


@router.get("/overview", dependencies=[Depends(require_admin_session)])
async def get_overview_telemetry(request: Request) -> dict[str, Any]:
    """Return consolidated telemetry across memories, files, conversations, and jobs."""
    async with request.app.state.session_factory() as session:
        # Memories
        active_memories = (
            await session.execute(
                select(func.count(MemoryRecord.id)).where(MemoryRecord.state == "active")
            )
        ).scalar_one()
        archived_memories = (
            await session.execute(
                select(func.count(MemoryRecord.id)).where(MemoryRecord.state != "active")
            )
        ).scalar_one()

        # Files
        active_files = (
            await session.execute(
                select(func.count(FileDocument.id)).where(FileDocument.tombstoned_at.is_(None))
            )
        ).scalar_one()
        tombstoned_files = (
            await session.execute(
                select(func.count(FileDocument.id)).where(FileDocument.tombstoned_at.is_not(None))
            )
        ).scalar_one()
        total_file_chars = (
            await session.execute(
                select(func.coalesce(func.sum(FileDocument.total_characters), 0)).where(
                    FileDocument.tombstoned_at.is_(None)
                )
            )
        ).scalar_one()

        # File Segments & Deduplication
        total_segments = (await session.execute(select(func.count(FileSegment.id)))).scalar_one()
        embedded_segments = (
            await session.execute(
                select(func.count(FileSegment.id)).where(FileSegment.embedding.is_not(None))
            )
        ).scalar_one()
        total_references = (
            await session.execute(
                select(func.count(FileReference.id)).where(FileReference.tombstoned_at.is_(None))
            )
        ).scalar_one()

        # Conversations
        total_turns = (
            await session.execute(
                select(func.count(CompletedTurn.id)).where(CompletedTurn.tombstoned_at.is_(None))
            )
        ).scalar_one()
        total_conv_segments = (
            await session.execute(select(func.count(ConversationSegment.id)))
        ).scalar_one()

        # Artifacts
        active_artifacts = (
            await session.execute(
                select(func.count(Artifact.id)).where(Artifact.tombstoned_at.is_(None))
            )
        ).scalar_one()
        # Topic Episodes
        total_episodes = (
            await session.execute(
                select(func.count(TopicEpisode.id)).where(TopicEpisode.tombstoned_at.is_(None))
            )
        ).scalar_one()

        total_artifact_versions = (
            await session.execute(select(func.count(ArtifactVersion.id)))
        ).scalar_one()

        # Media
        active_media = (
            await session.execute(
                select(func.count(MediaDocument.id)).where(MediaDocument.tombstoned_at.is_(None))
            )
        ).scalar_one()
        total_media_segments = (
            await session.execute(select(func.count(MediaSegment.id)))
        ).scalar_one()

        # Consolidation
        total_consolidation_runs = (
            await session.execute(select(func.count(ConsolidationRun.id)))
        ).scalar_one()
        total_consolidation_superseded = (
            await session.execute(
                select(func.coalesce(func.sum(ConsolidationRun.superseded_count), 0))
            )
        ).scalar_one()

        # KB Reconciliation
        total_kb_runs = (
            await session.execute(select(func.count(KBReconciliationRun.id)))
        ).scalar_one()
        total_kb_pruned = (
            await session.execute(
                select(func.coalesce(func.sum(KBReconciliationRun.pruned_count), 0))
            )
        ).scalar_one()

        # Jobs
        job_counts = dict(
            (
                await session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status))
            ).all()
        )

        return {
            "memories": {
                "active": active_memories,
                "archived": archived_memories,
                "total": active_memories + archived_memories,
            },
            "files": {
                "active": active_files,
                "tombstoned": tombstoned_files,
                "total": active_files + tombstoned_files,
                "total_characters": total_file_chars,
                "total_segments": total_segments,
                "embedded_segments": embedded_segments,
                "deduplicated_references": max(0, total_references - total_segments),
            },
            "total_episodes": total_episodes,
            "total_media": active_media,
            "media": {
                "active": active_media,
                "total_segments": total_media_segments,
            },
            "conversations": {
                "active_turns": total_turns,
                "indexed_passages": total_conv_segments,
            },
            "artifacts": {
                "active": active_artifacts,
                "total_versions": total_artifact_versions,
            },
            "consolidation": {
                "total_runs": total_consolidation_runs,
                "total_superseded": total_consolidation_superseded,
            },
            "kb_reconciliation": {
                "total_runs": total_kb_runs,
                "total_pruned": total_kb_pruned,
            },
            "jobs": {
                "queued": job_counts.get("queued", 0),
                "running": job_counts.get("running", 0),
                "completed": job_counts.get("completed", 0),
                "dead": job_counts.get("dead", 0),
            },
        }


# ---------------------------------------------------------------------------
# Memory Management
# ---------------------------------------------------------------------------


@router.get("/memories/categories", dependencies=[Depends(require_admin_session)])
async def list_memory_categories(request: Request) -> dict[str, list[str]]:
    """Return all distinct active memory categories present in the database."""
    async with request.app.state.session_factory() as session:
        stmt = (
            select(MemoryRecord.category)
            .where(MemoryRecord.state == "active")
            .distinct()
            .order_by(MemoryRecord.category.asc())
        )
        categories = list((await session.execute(stmt)).scalars().all())
        default_set = {
            "career",
            "decision",
            "fact",
            "homelab",
            "infrastructure",
            "learning",
            "preference",
            "project",
            "tooling",
        }
        all_categories = sorted(set(categories) | default_set)
        return {"categories": all_categories}


@router.get("/memories", dependencies=[Depends(require_admin_session)])
async def list_memories(
    request: Request,
    query: str | None = None,
    category: str | None = None,
    status_filter: Literal["active", "archived", "all"] = "active",
    timeline_filter: Literal[
        "all", "permanent", "ephemeral", "expired", "active_expiring", "upcoming"
    ] = "all",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Search and paginate memories with category, status, and timeline filters."""
    now = datetime.now(UTC)
    async with request.app.state.session_factory() as session:
        stmt = (
            select(
                MemoryRecord.id,
                MemoryRecord.statement,
                MemoryRecord.category,
                MemoryRecord.confidence,
                MemoryRecord.state,
                MemoryRecord.created_at,
                MemoryRecord.archived_at,
                UserIdentity.native_user_id,
                MemoryRecord.valid_from,
                MemoryRecord.expires_at,
                MemoryRecord.temporal_tag,
            )
            .join(UserIdentity, UserIdentity.id == MemoryRecord.user_id)
            .order_by(desc(MemoryRecord.created_at))
        )

        if status_filter == "active":
            stmt = stmt.where(MemoryRecord.state == "active")
        elif status_filter == "archived":
            stmt = stmt.where(MemoryRecord.state != "active")

        if timeline_filter == "permanent":
            stmt = stmt.where(MemoryRecord.expires_at.is_(None))
        elif timeline_filter == "ephemeral":
            stmt = stmt.where(MemoryRecord.expires_at.is_not(None))
        elif timeline_filter == "expired":
            stmt = stmt.where(
                MemoryRecord.expires_at.is_not(None),
                MemoryRecord.expires_at <= now,
            )
        elif timeline_filter == "active_expiring":
            stmt = stmt.where(
                MemoryRecord.expires_at.is_not(None),
                MemoryRecord.expires_at > now,
                MemoryRecord.valid_from.is_(None) | (MemoryRecord.valid_from <= now),
            )
        elif timeline_filter == "upcoming":
            stmt = stmt.where(
                MemoryRecord.valid_from.is_not(None),
                MemoryRecord.valid_from > now,
            )

        if category:
            stmt = stmt.where(MemoryRecord.category == category)

        if query and query.strip():
            like_term = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    MemoryRecord.statement.ilike(like_term),
                    MemoryRecord.category.ilike(like_term),
                    MemoryRecord.temporal_tag.ilike(like_term),
                    UserIdentity.native_user_id.ilike(like_term),
                )
            )

        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()

        rows = (await session.execute(stmt.limit(limit).offset(offset))).all()

        items = []
        for row in rows:
            rec_id = row[0]
            stmt_text = row[1]
            cat = row[2]
            conf = row[3]
            state = row[4]
            created = row[5]
            archived = row[6]
            native_uid = row[7]
            vfrom = row[8] if len(row) > 8 else None
            exp = row[9] if len(row) > 9 else None
            tag = row[10] if len(row) > 10 else None

            validity_status = "permanent"
            if state != "active":
                validity_status = "archived"
            elif exp is not None and exp <= now:
                validity_status = "expired"
            elif vfrom is not None and vfrom > now:
                validity_status = "upcoming"
            elif exp is not None:
                validity_status = "active_expiring"

            items.append(
                {
                    "id": str(rec_id),
                    "statement": stmt_text,
                    "category": cat,
                    "confidence": conf,
                    "state": state,
                    "created_at": created.isoformat() if created else None,
                    "archived_at": archived.isoformat() if archived else None,
                    "native_user_id": native_uid,
                    "valid_from": vfrom.isoformat() if vfrom else None,
                    "expires_at": exp.isoformat() if exp else None,
                    "temporal_tag": tag,
                    "validity_status": validity_status,
                }
            )

        return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.post("/memories", dependencies=[Depends(require_admin_session)])
async def create_memory(body: CreateMemoryRequest, request: Request) -> dict[str, Any]:
    """Manually create an explicit memory record."""
    now = datetime.now(UTC)
    async with request.app.state.session_factory() as session:
        # 1. Resolve or create user identity
        user_res = (
            await session.execute(
                select(UserIdentity.id).where(UserIdentity.native_user_id == body.native_user_id)
            )
        ).scalar_one_or_none()

        if user_res is None:
            user_id = uuid.uuid4()
            session.add(UserIdentity(id=user_id, native_user_id=body.native_user_id))
            await session.flush()
        else:
            user_id = user_res

        # 2. Create manual synthetic CompletedTurn for provenance
        turn_id = uuid.uuid4()
        user_content = body.evidence_quote or body.statement
        assistant_content = "Memory created via Admin Dashboard."
        session.add(
            CompletedTurn(
                id=turn_id,
                event_id=f"manual-admin-turn:{turn_id}",
                user_id=user_id,
                native_chat_id="manual-admin-entry",
                native_user_message_id="admin-manual",
                native_assistant_message_id="admin-manual",
                user_content=user_content,
                assistant_content=assistant_content,
                user_content_sha256=hashlib.sha256(user_content.encode("utf-8")).hexdigest(),
                assistant_content_sha256=hashlib.sha256(
                    assistant_content.encode("utf-8")
                ).hexdigest(),
                occurred_at=now,
            )
        )
        await session.flush()

        # 3. Create MemoryRecord & Evidence
        record_id = uuid.uuid4()
        category = (
            body.category
            if body.category in {"fact", "preference", "instruction", "project", "decision"}
            else "preference"
        )
        key = f"{category}:{uuid.uuid4().hex[:12]}"

        new_mem = MemoryRecord(
            id=record_id,
            user_id=user_id,
            key=key,
            kind="explicit",
            category=category,
            statement=body.statement,
            confidence=1,
            state="active",
            valid_from=body.valid_from,
            expires_at=body.expires_at,
            temporal_tag=body.temporal_tag,
            created_at=now,
        )
        session.add(new_mem)
        session.add(
            MemoryEvidence(
                id=uuid.uuid4(),
                memory_record_id=record_id,
                completed_turn_id=turn_id,
                native_user_message_id="admin-manual",
                evidence_quote=body.evidence_quote or body.statement,
            )
        )
        await log_memory_change(
            session,
            native_user_id=body.native_user_id,
            user_id=user_id,
            memory_id=record_id,
            change_source="admin_ui",
            action="create",
            previous_state=None,
            new_state=_memory_snapshot(new_mem),
            reason="Manually created from Admin UI",
        )
        await session.commit()

        return {
            "id": str(record_id),
            "native_user_id": body.native_user_id,
            "statement": body.statement,
            "category": category,
            "state": "active",
            "valid_from": body.valid_from.isoformat() if body.valid_from else None,
            "expires_at": body.expires_at.isoformat() if body.expires_at else None,
            "temporal_tag": body.temporal_tag,
            "created_at": now.isoformat(),
        }


@router.patch("/memories/{memory_id}", dependencies=[Depends(require_admin_session)])
async def update_memory(
    memory_id: uuid.UUID, body: UpdateMemoryRequest, request: Request
) -> dict[str, Any]:
    """Update memory statement, category, temporal bounds, or active state."""
    async with request.app.state.session_factory() as session:
        record = (
            await session.execute(select(MemoryRecord).where(MemoryRecord.id == memory_id))
        ).scalar_one_or_none()

        if record is None:
            raise HTTPException(status_code=404, detail="memory not found")

        prev_snap = _memory_snapshot(record)
        user_identity = (
            await session.execute(select(UserIdentity).where(UserIdentity.id == record.user_id))
        ).scalar_one_or_none()
        native_user_id = user_identity.native_user_id if user_identity else "admin"

        if body.statement is not None:
            record.statement = body.statement
        if body.category is not None and body.category in {
            "fact",
            "preference",
            "instruction",
            "project",
            "decision",
        }:
            record.category = body.category
        if body.valid_from is not None:
            record.valid_from = body.valid_from
        if body.expires_at is not None:
            record.expires_at = body.expires_at
        if body.temporal_tag is not None:
            record.temporal_tag = body.temporal_tag
        if body.state is not None:
            if body.state == "tombstoned":
                record.state = "archived"
                record.archived_at = datetime.now(UTC)
            elif body.state == "active":
                record.state = "active"
                record.archived_at = None

        await log_memory_change(
            session,
            native_user_id=native_user_id,
            user_id=record.user_id,
            memory_id=record.id,
            change_source="admin_ui",
            action="update",
            previous_state=prev_snap,
            new_state=_memory_snapshot(record),
            reason="Manually updated from Admin UI",
        )
        await session.commit()
        return {
            "id": str(record.id),
            "statement": record.statement,
            "category": record.category,
            "state": record.state,
            "valid_from": record.valid_from.isoformat() if record.valid_from else None,
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            "temporal_tag": record.temporal_tag,
            "archived_at": record.archived_at.isoformat() if record.archived_at else None,
        }


@router.delete("/memories/{memory_id}", dependencies=[Depends(require_admin_session)])
async def delete_memory(memory_id: uuid.UUID, request: Request) -> dict[str, str]:
    """Deactivate (archive) a memory record."""
    async with request.app.state.session_factory() as session:
        record = (
            await session.execute(select(MemoryRecord).where(MemoryRecord.id == memory_id))
        ).scalar_one_or_none()

        if record is None:
            raise HTTPException(status_code=404, detail="memory not found")

        prev_snap = _memory_snapshot(record)
        user_identity = (
            await session.execute(select(UserIdentity).where(UserIdentity.id == record.user_id))
        ).scalar_one_or_none()
        native_user_id = user_identity.native_user_id if user_identity else "admin"

        record.state = "archived"
        record.archived_at = datetime.now(UTC)

        await log_memory_change(
            session,
            native_user_id=native_user_id,
            user_id=record.user_id,
            memory_id=record.id,
            change_source="admin_ui",
            action="archive",
            previous_state=prev_snap,
            new_state=_memory_snapshot(record),
            reason="Manually deleted/archived from Admin UI",
        )
        await session.commit()
        return {"status": "archived", "id": str(memory_id)}


# ---------------------------------------------------------------------------
# File Document & Chunk Management
# ---------------------------------------------------------------------------


@router.get("/files", dependencies=[Depends(require_admin_session)])
async def list_files(
    request: Request,
    query: str | None = None,
    status_filter: Literal["active", "tombstoned", "all"] = "active",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List indexed files with metadata, status, chunk and char counts."""
    async with request.app.state.session_factory() as session:
        stmt = (
            select(
                FileDocument.id,
                FileDocument.native_file_id,
                FileDocument.filename,
                FileDocument.mime_type,
                FileDocument.total_chunks,
                FileDocument.total_characters,
                FileDocument.created_at,
                FileDocument.tombstoned_at,
                UserIdentity.native_user_id,
            )
            .join(UserIdentity, UserIdentity.id == FileDocument.user_id)
            .order_by(desc(FileDocument.created_at))
        )

        if status_filter == "active":
            stmt = stmt.where(FileDocument.tombstoned_at.is_(None))
        elif status_filter == "tombstoned":
            stmt = stmt.where(FileDocument.tombstoned_at.is_not(None))

        if query and query.strip():
            like_term = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    FileDocument.filename.ilike(like_term),
                    FileDocument.native_file_id.ilike(like_term),
                    UserIdentity.native_user_id.ilike(like_term),
                )
            )

        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()

        rows = (await session.execute(stmt.limit(limit).offset(offset))).all()

        items = [
            {
                "id": str(row[0]),
                "native_file_id": row[1],
                "filename": row[2],
                "mime_type": row[3],
                "total_chunks": row[4],
                "total_characters": row[5],
                "created_at": row[6].isoformat() if row[6] else None,
                "tombstoned_at": row[7].isoformat() if row[7] else None,
                "status": "tombstoned" if row[7] is not None else "active",
                "native_user_id": row[8],
            }
            for row in rows
        ]

        return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.get("/files/{native_file_id}", dependencies=[Depends(require_admin_session)])
async def get_file_detail(native_file_id: str, request: Request) -> dict[str, Any]:
    """Fetch complete un-chunked document markdown and chunk segment breakdown."""
    async with request.app.state.session_factory() as session:
        doc = (
            await session.execute(
                select(FileDocument).where(FileDocument.native_file_id == native_file_id)
            )
        ).scalar_one_or_none()

        if doc is None:
            raise HTTPException(status_code=404, detail="file document not found")

        chunks_stmt = (
            select(
                FileReference.chunk_ordinal,
                FileReference.header_path,
                FileSegment.content_sha256,
                func.char_length(FileSegment.content),
                FileSegment.embedding.is_not(None),
            )
            .join(FileSegment, FileReference.segment_id == FileSegment.id)
            .where(
                FileReference.native_file_id == native_file_id,
                FileReference.user_id == doc.user_id,
            )
            .order_by(FileReference.chunk_ordinal.asc())
        )
        chunk_rows = (await session.execute(chunks_stmt)).all()

        chunks = [
            {
                "ordinal": row[0],
                "header_path": row[1],
                "content_sha256": row[2],
                "char_length": row[3],
                "has_embedding": row[4],
            }
            for row in chunk_rows
        ]

        return {
            "id": str(doc.id),
            "native_file_id": doc.native_file_id,
            "filename": doc.filename,
            "mime_type": doc.mime_type,
            "total_chunks": doc.total_chunks,
            "total_characters": doc.total_characters,
            "content": doc.content,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "tombstoned_at": doc.tombstoned_at.isoformat() if doc.tombstoned_at else None,
            "chunks": chunks,
        }


@router.delete("/files/{native_file_id}", dependencies=[Depends(require_admin_session)])
async def delete_file(native_file_id: str, request: Request) -> dict[str, Any]:
    """Tombstone a file document and its associated references."""
    async with request.app.state.session_factory() as session:
        doc = (
            await session.execute(
                select(FileDocument).where(FileDocument.native_file_id == native_file_id)
            )
        ).scalar_one_or_none()

        if doc is None:
            raise HTTPException(status_code=404, detail="file document not found")

        count = await tombstone_file_references(
            session, user_id=doc.user_id, native_file_id=native_file_id
        )
        await session.commit()
        return {
            "status": "tombstoned",
            "native_file_id": native_file_id,
            "references_tombstoned": count,
        }


# ---------------------------------------------------------------------------
# Conversation Recall Inspection
# ---------------------------------------------------------------------------


@router.get("/conversations", dependencies=[Depends(require_admin_session)])
async def list_conversations(
    request: Request,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Search and inspect indexed conversation turns and context."""
    async with request.app.state.session_factory() as session:
        stmt = (
            select(
                CompletedTurn.id,
                CompletedTurn.native_chat_id,
                CompletedTurn.native_user_message_id,
                CompletedTurn.user_content,
                CompletedTurn.assistant_content,
                CompletedTurn.occurred_at,
                UserIdentity.native_user_id,
            )
            .join(UserIdentity, UserIdentity.id == CompletedTurn.user_id)
            .where(CompletedTurn.tombstoned_at.is_(None))
            .order_by(desc(CompletedTurn.occurred_at))
        )

        if query and query.strip():
            like_term = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    CompletedTurn.user_content.ilike(like_term),
                    CompletedTurn.assistant_content.ilike(like_term),
                    CompletedTurn.native_chat_id.ilike(like_term),
                    UserIdentity.native_user_id.ilike(like_term),
                )
            )

        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()

        rows = (await session.execute(stmt.limit(limit).offset(offset))).all()

        items = [
            {
                "id": str(row[0]),
                "native_chat_id": row[1],
                "native_message_id": row[2],
                "user_content": row[3],
                "assistant_content": row[4],
                "occurred_at": row[5].isoformat() if row[5] else None,
                "native_user_id": row[6],
            }
            for row in rows
        ]

        return {"total": total, "items": items, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# Background Job Queue Observability & Control
# ---------------------------------------------------------------------------


@router.get("/jobs", dependencies=[Depends(require_admin_session)])
async def list_jobs(
    request: Request,
    status_filter: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List recent background jobs with retry and failure info."""
    async with request.app.state.session_factory() as session:
        stmt = select(Job).order_by(desc(Job.available_at))

        if status_filter:
            stmt = stmt.where(Job.status == status_filter)
        if kind:
            stmt = stmt.where(Job.kind == kind)

        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()

        jobs = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()

        items = [
            {
                "id": str(j.id),
                "kind": j.kind,
                "identity_key": j.identity_key,
                "status": j.status,
                "attempts": j.attempts,
                "last_error": j.last_error_code,
                "available_at": j.available_at.isoformat() if j.available_at else None,
                "claimed_at": j.claimed_at.isoformat() if j.claimed_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in jobs
        ]

        return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.post("/jobs/{job_id}/retry", dependencies=[Depends(require_admin_session)])
async def retry_job(job_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Re-queue a dead or failed job for immediate retry."""
    now = datetime.now(UTC)
    async with request.app.state.session_factory() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()

        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        job.status = "queued"
        job.attempts = 0
        job.last_error_code = None
        job.available_at = now
        job.claimed_at = None

        await session.commit()
        return {"status": "requeued", "job_id": str(job.id)}


# ---------------------------------------------------------------------------
# Batch Operations & System Purge
# ---------------------------------------------------------------------------


@router.post("/memories/batch-delete", dependencies=[Depends(require_admin_session)])
async def batch_delete_memories(body: BatchDeleteRequest, request: Request) -> dict[str, Any]:
    """Permanently delete a batch of memory records and their evidence."""
    async with request.app.state.session_factory() as session:
        await session.execute(
            sa_delete(MemoryEvidence).where(MemoryEvidence.memory_record_id.in_(body.ids))
        )
        await session.execute(
            update(MemoryRecord)
            .where(MemoryRecord.superseded_by_id.in_(body.ids))
            .values(superseded_by_id=None)
        )
        res = await session.execute(sa_delete(MemoryRecord).where(MemoryRecord.id.in_(body.ids)))
        deleted_count = res.rowcount or 0
        await session.commit()
        return {"status": "deleted", "count": deleted_count}


@router.post("/files/batch-delete", dependencies=[Depends(require_admin_session)])
async def batch_delete_files(body: BatchDeleteFilesRequest, request: Request) -> dict[str, Any]:
    """Permanently delete a batch of file documents and their references."""
    async with request.app.state.session_factory() as session:
        await session.execute(
            sa_delete(FileReference).where(FileReference.native_file_id.in_(body.native_file_ids))
        )
        res = await session.execute(
            sa_delete(FileDocument).where(FileDocument.native_file_id.in_(body.native_file_ids))
        )
        orphan_stmt = sa_delete(FileSegment).where(
            ~FileSegment.id.in_(select(FileReference.segment_id))
        )
        await session.execute(orphan_stmt)

        deleted_count = res.rowcount or 0
        await session.commit()
        return {"status": "deleted", "count": deleted_count}


@router.post("/conversations/batch-delete", dependencies=[Depends(require_admin_session)])
async def batch_delete_conversations(body: BatchDeleteRequest, request: Request) -> dict[str, Any]:
    """Permanently delete a batch of conversation turns and their references."""
    async with request.app.state.session_factory() as session:
        await session.execute(
            sa_delete(MemoryEvidence).where(MemoryEvidence.completed_turn_id.in_(body.ids))
        )
        await session.execute(
            sa_delete(ConversationReference).where(
                ConversationReference.completed_turn_id.in_(body.ids)
            )
        )
        res = await session.execute(sa_delete(CompletedTurn).where(CompletedTurn.id.in_(body.ids)))
        orphan_stmt = sa_delete(ConversationSegment).where(
            ~ConversationSegment.id.in_(select(ConversationReference.segment_id))
        )
        await session.execute(orphan_stmt)

        deleted_count = res.rowcount or 0
        await session.commit()
        return {"status": "deleted", "count": deleted_count}


@router.post("/system/purge", dependencies=[Depends(require_admin_session)])
async def purge_system_data(body: PurgeSystemRequest, request: Request) -> dict[str, Any]:
    """Permanently purge data across assistant-core with confirmation validation."""
    if body.confirmation.strip().upper() != "PURGE":
        raise HTTPException(
            status_code=400,
            detail="Confirmation phrase mismatch. You must provide confirmation='PURGE' to execute.",
        )

    scope = body.scope
    deleted_counts: dict[str, int] = {}

    async with request.app.state.session_factory() as session:
        if scope in ("memories", "all"):
            ev_count = (await session.execute(sa_delete(MemoryEvidence))).rowcount or 0
            await session.execute(update(MemoryRecord).values(superseded_by_id=None))
            rec_count = (await session.execute(sa_delete(MemoryRecord))).rowcount or 0
            snap_count = (await session.execute(sa_delete(ChatProfileSnapshot))).rowcount or 0
            deleted_counts["memories"] = rec_count
            deleted_counts["evidence"] = ev_count
            deleted_counts["snapshots"] = snap_count

        if scope in ("files", "all"):
            ref_count = (await session.execute(sa_delete(FileReference))).rowcount or 0
            doc_count = (await session.execute(sa_delete(FileDocument))).rowcount or 0
            seg_count = (await session.execute(sa_delete(FileSegment))).rowcount or 0
            deleted_counts["file_references"] = ref_count
            deleted_counts["file_documents"] = doc_count
            deleted_counts["file_segments"] = seg_count

        if scope in ("conversations", "all"):
            if scope == "conversations":
                await session.execute(sa_delete(MemoryEvidence))
            conv_ref_count = (await session.execute(sa_delete(ConversationReference))).rowcount or 0
            conv_seg_count = (await session.execute(sa_delete(ConversationSegment))).rowcount or 0
            turn_count = (await session.execute(sa_delete(CompletedTurn))).rowcount or 0
            deleted_counts["conversation_references"] = conv_ref_count
            deleted_counts["conversation_segments"] = conv_seg_count
            deleted_counts["completed_turns"] = turn_count

        if scope in ("artifacts", "all"):
            ver_count = (await session.execute(sa_delete(ArtifactVersion))).rowcount or 0
            sess_count = (await session.execute(sa_delete(OnlyOfficeSession))).rowcount or 0
            art_count = (await session.execute(sa_delete(Artifact))).rowcount or 0
            deleted_counts["artifacts"] = art_count
            deleted_counts["artifact_versions"] = ver_count
            deleted_counts["onlyoffice_sessions"] = sess_count

        if scope in ("episodes", "all"):
            ep_count = (await session.execute(sa_delete(TopicEpisode))).rowcount or 0
            deleted_counts["episodes"] = ep_count

        if scope in ("media", "all"):
            seg_count = (await session.execute(sa_delete(MediaSegment))).rowcount or 0
            doc_count = (await session.execute(sa_delete(MediaDocument))).rowcount or 0
            deleted_counts["media_segments"] = seg_count
            deleted_counts["media_documents"] = doc_count

        if scope in ("jobs", "all"):
            job_count = (await session.execute(sa_delete(Job))).rowcount or 0
            inbox_count = (await session.execute(sa_delete(EventInbox))).rowcount or 0
            deleted_counts["jobs"] = job_count
            deleted_counts["events"] = inbox_count

    return {
        "status": "purged",
        "scope": scope,
        "deleted": deleted_counts,
    }


# ---------------------------------------------------------------------------
# Admin Artifact Management Routes
# ---------------------------------------------------------------------------


@router.get("/artifacts", dependencies=[Depends(require_admin_session)])
async def list_admin_artifacts(
    request: Request,
    user_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List all artifacts across the system or for a specific user."""
    async with request.app.state.session_factory() as session:
        query = (
            select(Artifact, UserIdentity.native_user_id)
            .join(UserIdentity, UserIdentity.id == Artifact.user_id)
            .where(Artifact.tombstoned_at.is_(None))
            .options(selectinload(Artifact.versions))
            .order_by(desc(Artifact.updated_at))
        )
        if user_id:
            query = query.where(UserIdentity.native_user_id == user_id)

        total_stmt = (
            select(func.count(Artifact.id))
            .join(UserIdentity, UserIdentity.id == Artifact.user_id)
            .where(Artifact.tombstoned_at.is_(None))
        )
        if user_id:
            total_stmt = total_stmt.where(UserIdentity.native_user_id == user_id)

        total = (await session.execute(total_stmt)).scalar_one()
        rows = (await session.execute(query.limit(limit).offset(offset))).all()

        base_url = str(request.base_url)
        items = []
        for art, native_uid in rows:
            versions = [
                {
                    "version_num": v.version_num,
                    "content_sha256": v.content_sha256,
                    "file_size_bytes": v.file_size_bytes,
                    "mime_type": v.mime_type,
                    "change_summary": v.change_summary,
                    "created_at": v.created_at.isoformat() if v.created_at else "",
                }
                for v in art.versions
            ]
            items.append(
                {
                    "id": str(art.id),
                    "native_user_id": native_uid,
                    "title": art.title,
                    "slug": art.slug,
                    "artifact_type": art.artifact_type,
                    "current_version_num": art.current_version_num,
                    "created_at": art.created_at.isoformat() if art.created_at else "",
                    "updated_at": art.updated_at.isoformat() if art.updated_at else "",
                    "versions": versions,
                    "download_url": f"{base_url.rstrip('/')}/v1/artifacts/{art.id}/download",
                }
            )

        return {"total": total, "artifacts": items}


@router.delete("/artifacts/{artifact_id}", dependencies=[Depends(require_admin_session)])
async def delete_admin_artifact(artifact_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Soft delete an artifact from the admin dashboard."""
    async with request.app.state.session_factory() as session:
        stmt = (
            update(Artifact)
            .where(Artifact.id == artifact_id)
            .values(tombstoned_at=datetime.now(UTC))
        )
        res = await session.execute(stmt)
        await session.commit()
        if not res.rowcount:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return {"status": "deleted", "artifact_id": str(artifact_id)}


@router.post(
    "/artifacts/{artifact_id}/onlyoffice/session", dependencies=[Depends(require_admin_session)]
)
async def create_admin_onlyoffice_session(
    artifact_id: uuid.UUID, request: Request
) -> dict[str, Any]:
    """Create an OnlyOffice editor session from the admin dashboard."""
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        art = await repo.get_artifact(artifact_id)
        if not art or art.tombstoned_at is not None:
            raise HTTPException(status_code=404, detail="Artifact not found")

        target_v = next((v for v in art.versions if v.version_num == art.current_version_num), None)
        if not target_v:
            raise HTTPException(status_code=404, detail="Active version not found")

        # Get native user id
        user_stmt = select(UserIdentity.native_user_id).where(UserIdentity.id == art.user_id)
        native_user_id = (await session.execute(user_stmt)).scalar_one()

        manager = OnlyOfficeManager(session, repo, settings)
        base_url = str(request.base_url)
        config = await manager.create_editor_session(
            artifact=art,
            current_version=target_v,
            native_user_id=native_user_id,
            base_service_url=base_url,
        )
        await session.commit()
        return {
            "onlyoffice_url": settings.onlyoffice_url,
            "config": config,
        }


@router.get("/artifacts/{artifact_id}/diff", dependencies=[Depends(require_admin_session)])
async def get_artifact_version_diff(
    artifact_id: uuid.UUID,
    request: Request,
    v1: int = Query(..., ge=1, description="Base version number"),
    v2: int = Query(..., ge=1, description="Target version number to compare against"),
) -> dict[str, Any]:
    """Compute unified diff between two versions of an artifact."""
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        art = await repo.get_artifact(artifact_id)
        if not art or art.tombstoned_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

        ver1 = next((v for v in art.versions if v.version_num == v1), None)
        if not ver1:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Version {v1} not found"
            )

        ver2 = next((v for v in art.versions if v.version_num == v2), None)
        if not ver2:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Version {v2} not found"
            )

        lines1 = extract_artifact_text(art.artifact_type, ver1.binary_data)
        lines2 = extract_artifact_text(art.artifact_type, ver2.binary_data)
        return compute_unified_diff(lines1, lines2, v1, v2)


@router.post("/artifacts/{artifact_id}/revert", dependencies=[Depends(require_admin_session)])
async def revert_admin_artifact(
    artifact_id: uuid.UUID,
    body: RevertArtifactRequest,
    request: Request,
) -> dict[str, Any]:
    """Revert an artifact to a historical version by creating a new version N+1."""
    async with request.app.state.session_factory() as session:
        repo = ArtifactRepository(session)
        try:
            artifact, version = await repo.revert_to_version(
                artifact_id=artifact_id,
                target_version_num=body.target_version_num,
            )
            await session.commit()
            return {
                "status": "reverted",
                "artifact_id": str(artifact.id),
                "target_version_num": body.target_version_num,
                "new_version_num": version.version_num,
                "current_version_num": artifact.current_version_num,
                "change_summary": version.change_summary,
            }
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except PermissionError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to revert artifact: {exc}",
            ) from exc


async def _embed_query(request: Request, query: str) -> list[float] | None:
    """Embed a query, failing open to lexical-only search if embeddings are unavailable."""
    try:
        embedder = get_conversation_embedder(request.app.state.settings)
        return await asyncio.to_thread(embedder.embed_one, query)
    except (EmbeddingConfigurationError, ConversationEmbeddingError):
        return None


@router.post("/memories/consolidate", dependencies=[Depends(require_admin_session)])
async def trigger_memory_consolidation(
    body: ConsolidateMemoriesRequest, request: Request
) -> dict[str, Any]:
    """Trigger conflict resolution and memory consolidation for one or all users."""
    try:
        consolidator = get_memory_consolidator(request.app.state.settings)
    except TaskModelConfigurationError:
        raise HTTPException(
            status_code=400,
            detail="Task model is not configured. Please configure ASSISTANT_TASK_MODEL_BASE_URL and ASSISTANT_TASK_MODEL_MODEL in your environment settings.",
        ) from None

    applied_all: list[dict[str, Any]] = []
    users: list[str] = []

    try:
        async with request.app.state.session_factory() as session:
            if body.native_user_id:
                users = [body.native_user_id]
            else:
                user_stmt = select(UserIdentity.native_user_id).distinct()
                users = list((await session.execute(user_stmt)).scalars().all())

            for u in users:
                applied = await consolidate_user_memories(
                    session,
                    native_user_id=u,
                    consolidator=consolidator,
                    trigger="manual_admin",
                )
                applied_all.extend(applied)

            await session.commit()
    except MemoryConsolidationError as exc:
        LOGGER.error("memory_consolidation_llm_failed", error=str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"Memory consolidation failed during LLM evaluation: {exc}",
        ) from None
    except Exception as exc:
        LOGGER.exception("memory_consolidation_unexpected_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Memory consolidation failed: {exc}",
        ) from None

    return {
        "status": "success",
        "consolidated_users": len(users),
        "total_superseded": len(applied_all),
        "details": applied_all,
    }


@router.get("/consolidation-runs", dependencies=[Depends(require_admin_session)])
async def list_consolidation_runs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    native_user_id: str | None = Query(default=None),
    trigger: str | None = Query(default=None),
) -> dict[str, Any]:
    """List historical memory consolidation runs with audit details."""
    async with request.app.state.session_factory() as session:
        query = select(ConsolidationRun)
        count_query = select(func.count(ConsolidationRun.id))

        if native_user_id:
            query = query.where(ConsolidationRun.native_user_id == native_user_id)
            count_query = count_query.where(ConsolidationRun.native_user_id == native_user_id)

        if trigger:
            query = query.where(ConsolidationRun.trigger == trigger)
            count_query = count_query.where(ConsolidationRun.trigger == trigger)

        total = (await session.execute(count_query)).scalar_one()

        offset = (page - 1) * page_size
        runs = list(
            (
                await session.execute(
                    query.order_by(ConsolidationRun.created_at.desc())
                    .offset(offset)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )

        items = [
            {
                "id": str(r.id),
                "user_id": str(r.user_id) if r.user_id else None,
                "native_user_id": r.native_user_id,
                "trigger": r.trigger,
                "status": r.status,
                "memories_scanned": r.memories_scanned,
                "superseded_count": r.superseded_count,
                "details": r.details,
                "error_message": r.error_message,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }


@router.get("/consolidation-runs/{run_id}", dependencies=[Depends(require_admin_session)])
async def get_consolidation_run(run_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Get single consolidation run by ID with full diff details."""
    async with request.app.state.session_factory() as session:
        run = (
            await session.execute(select(ConsolidationRun).where(ConsolidationRun.id == run_id))
        ).scalar_one_or_none()

        if run is None:
            raise HTTPException(status_code=404, detail="consolidation run not found")

        return {
            "id": str(run.id),
            "user_id": str(run.user_id) if run.user_id else None,
            "native_user_id": run.native_user_id,
            "trigger": run.trigger,
            "status": run.status,
            "memories_scanned": run.memories_scanned,
            "superseded_count": run.superseded_count,
            "details": run.details,
            "error_message": run.error_message,
            "duration_ms": run.duration_ms,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        }


@router.post(
    "/consolidation-runs/{run_id}/revert-item/{item_index}",
    dependencies=[Depends(require_admin_session)],
)
async def revert_consolidation_run_item(
    run_id: uuid.UUID, item_index: int, request: Request
) -> dict[str, Any]:
    """Revert a single decision item from a historical consolidation run."""
    async with request.app.state.session_factory() as session:
        try:
            result = await revert_consolidation_item(session, run_id=run_id, item_index=item_index)
            await session.commit()
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/memory-changes", dependencies=[Depends(require_admin_session)])
async def list_memory_changes(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    native_user_id: str | None = None,
    memory_id: uuid.UUID | None = None,
    change_source: str | None = None,
) -> dict[str, Any]:
    """List historical memory changes across all sources with pagination."""
    async with request.app.state.session_factory() as session:
        items, total = await list_memory_change_logs(
            session,
            native_user_id=native_user_id,
            memory_id=memory_id,
            change_source=change_source,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [
                {
                    "id": str(log.id),
                    "user_id": str(log.user_id) if log.user_id else None,
                    "native_user_id": log.native_user_id,
                    "memory_id": str(log.memory_id) if log.memory_id else None,
                    "change_source": log.change_source,
                    "action": log.action,
                    "previous_state": log.previous_state,
                    "new_state": log.new_state,
                    "reason": log.reason,
                    "is_reverted": log.is_reverted,
                    "reverted_at": log.reverted_at.isoformat() if log.reverted_at else None,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


@router.post(
    "/memory-changes/{log_id}/revert",
    dependencies=[Depends(require_admin_session)],
)
async def revert_memory_change(log_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Revert a memory change log entry, restoring previous state."""
    async with request.app.state.session_factory() as session:
        try:
            result = await revert_memory_change_log(session, log_id=log_id)
            await session.commit()
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Knowledge Base Document Reconciliation & History
# ---------------------------------------------------------------------------


@router.post("/files/reconcile-kb", dependencies=[Depends(require_admin_session)])
async def trigger_kb_reconciliation(request: Request) -> dict[str, Any]:
    """Trigger immediate on-demand Knowledge Base document reconciliation."""
    settings = request.app.state.settings
    api_key = (
        settings.open_webui_api_key.get_secret_value() if settings.open_webui_api_key else None
    )
    oikb_api_key = settings.oikb_api_key.get_secret_value() if settings.oikb_api_key else None

    async with request.app.state.session_factory() as session:
        run = await reconcile_and_log_kb_documents(
            session,
            base_url=settings.open_webui_url,
            api_key=api_key,
            oikb_url=settings.oikb_url,
            oikb_api_key=oikb_api_key,
            trigger="manual_admin",
            timeout_seconds=settings.file_indexing_timeout_seconds,
        )
        return {
            "id": str(run.id),
            "trigger": run.trigger,
            "status": run.status,
            "kb_files_scanned": run.kb_files_scanned,
            "pruned_count": run.pruned_count,
            "details": run.details,
            "error_message": run.error_message,
            "duration_ms": run.duration_ms,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        }


@router.get("/files/reconcile-kb/runs", dependencies=[Depends(require_admin_session)])
async def list_kb_reconciliation_runs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    trigger: str | None = None,
) -> dict[str, Any]:
    """List paginated history of KB document reconciliation runs."""
    async with request.app.state.session_factory() as session:
        query = select(KBReconciliationRun)
        count_query = select(func.count(KBReconciliationRun.id))

        if trigger:
            query = query.where(KBReconciliationRun.trigger == trigger)
            count_query = count_query.where(KBReconciliationRun.trigger == trigger)

        total = (await session.execute(count_query)).scalar_one()

        runs_stmt = (
            query.order_by(desc(KBReconciliationRun.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        runs = (await session.execute(runs_stmt)).scalars().all()

        items = [
            {
                "id": str(r.id),
                "trigger": r.trigger,
                "status": r.status,
                "kb_files_scanned": r.kb_files_scanned,
                "pruned_count": r.pruned_count,
                "details": r.details,
                "error_message": r.error_message,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }


@router.get("/files/reconcile-kb/runs/{run_id}", dependencies=[Depends(require_admin_session)])
async def get_kb_reconciliation_run(run_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Get single KB reconciliation run details."""
    async with request.app.state.session_factory() as session:
        run = (
            await session.execute(
                select(KBReconciliationRun).where(KBReconciliationRun.id == run_id)
            )
        ).scalar_one_or_none()

        if run is None:
            raise HTTPException(status_code=404, detail="kb reconciliation run not found")

        return {
            "id": str(run.id),
            "trigger": run.trigger,
            "status": run.status,
            "kb_files_scanned": run.kb_files_scanned,
            "pruned_count": run.pruned_count,
            "details": run.details,
            "error_message": run.error_message,
            "duration_ms": run.duration_ms,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        }


@router.get("/episodes", dependencies=[Depends(require_admin_session)])
async def list_admin_topic_episodes(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    native_chat_id: str | None = None,
    include_tombstoned: bool = False,
) -> dict[str, Any]:
    """List topic episodes with paging and filtering."""
    async with request.app.state.session_factory() as session:
        episodes = await list_topic_episodes(
            session,
            native_chat_id=native_chat_id,
            include_tombstoned=include_tombstoned,
            limit=limit,
            offset=offset,
        )
        total_stmt = select(func.count(TopicEpisode.id))
        if not include_tombstoned:
            total_stmt = total_stmt.where(TopicEpisode.tombstoned_at.is_(None))
        total_count = (await session.execute(total_stmt)).scalar_one()

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "episodes": [ep.model_dump(mode="json") for ep in episodes],
    }


@router.get("/episodes/{episode_id}", dependencies=[Depends(require_admin_session)])
async def get_admin_topic_episode(episode_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Get single topic episode details."""
    async with request.app.state.session_factory() as session:
        detail = await get_topic_episode(session, episode_id=episode_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="topic episode not found")
        return detail.model_dump(mode="json")


@router.post("/episodes/compile", dependencies=[Depends(require_admin_session)])
async def compile_admin_topic_episodes(
    body: CompileEpisodesRequest, request: Request
) -> dict[str, Any]:
    """Trigger on-demand topic episode compilation for a chat or all eligible chats."""
    started_at = perf_counter()
    async with request.app.state.session_factory() as session:
        if body.native_chat_id:
            user_row = (
                await session.execute(
                    select(UserIdentity.id).where(
                        UserIdentity.native_user_id == body.native_user_id
                    )
                )
            ).scalar_one_or_none()
            if user_row is None:
                raise HTTPException(status_code=404, detail="user not found")
            chats = [(user_row, body.native_user_id or "user-1", body.native_chat_id)]
        else:
            chats = await detect_inactive_chats_for_compilation(
                session, inactivity_hours=0.0, limit=10
            )

        if not chats:
            return {"status": "no_eligible_chats", "compiled_count": 0, "duration_ms": 0.0}

        compiled_total = 0
        total_turns = 0
        extractor = get_episode_extractor(request.app.state.settings)
        embedder = None
        try:
            embedder = get_conversation_embedder(request.app.state.settings)
        except Exception:  # noqa: BLE001
            embedder = None

        for user_id, _native_uid, native_chat_id in chats:
            turns = await get_uncompiled_turns_for_chat(
                session, user_id=user_id, native_chat_id=native_chat_id
            )
            if not turns:
                continue
            turn_inputs = [
                TurnSummaryInput(
                    user_message_id=t.native_user_message_id,
                    assistant_message_id=t.native_assistant_message_id,
                    user_content=t.user_content,
                    assistant_content=t.assistant_content,
                    occurred_at=t.occurred_at,
                )
                for t in turns
            ]
            try:
                extractions = await extractor.extract_episodes(turn_inputs)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "admin_compile_episodes_failed", chat_id=native_chat_id, error=str(exc)
                )
                continue

            for ext in extractions:
                vector = None
                if embedder is not None:
                    text_to_embed = (
                        f"Title: {ext.title}\n"
                        f"Category: {ext.topic_category}\n"
                        f"Summary: {ext.summary}\n"
                        f"Decisions: {', '.join(ext.decisions_made)}\n"
                        f"Entities: {', '.join(ext.key_entities)}"
                    )
                    try:
                        vector = await asyncio.to_thread(embedder.embed_one, text_to_embed)
                    except Exception:  # noqa: BLE001
                        vector = None
                await create_topic_episode(
                    session,
                    user_id=user_id,
                    native_chat_id=native_chat_id,
                    native_project_id=turns[0].native_project_id,
                    native_folder_id=turns[0].native_folder_id,
                    extraction=ext,
                    turn_count=len(turns),
                    embedding=vector,
                )
                compiled_total += 1
            total_turns += len(turns)

        await session.commit()

    duration_ms = round((perf_counter() - started_at) * 1000, 2)
    return {
        "status": "completed",
        "chats_processed": len(chats),
        "total_turns_compiled": total_turns,
        "episodes_created": compiled_total,
        "duration_ms": duration_ms,
    }


@router.delete("/episodes/{episode_id}", dependencies=[Depends(require_admin_session)])
async def delete_admin_topic_episode(episode_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Delete a topic episode."""
    async with request.app.state.session_factory() as session:
        deleted = await delete_topic_episode(session, episode_id=episode_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="topic episode not found")
        await session.commit()
    return {"status": "deleted", "id": str(episode_id)}



# ---------------------------------------------------------------------------
# Media Understanding & Segment Management
# ---------------------------------------------------------------------------


@router.get("/media", dependencies=[Depends(require_admin_session)])
async def list_admin_media(
    request: Request,
    query: str | None = None,
    status_filter: Literal["all", "active", "tombstoned"] = "active",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List indexed media documents with segment counters, metadata, and status."""
    async with request.app.state.session_factory() as session:
        stmt = (
            select(
                MediaDocument.id,
                MediaDocument.url,
                MediaDocument.media_type,
                MediaDocument.title,
                MediaDocument.channel_or_author,
                MediaDocument.duration_seconds,
                MediaDocument.summary,
                MediaDocument.key_takeaways,
                MediaDocument.topics,
                MediaDocument.total_segments,
                MediaDocument.embedding.is_not(None),
                MediaDocument.created_at,
                MediaDocument.updated_at,
                MediaDocument.tombstoned_at,
                UserIdentity.native_user_id,
            )
            .join(UserIdentity, UserIdentity.id == MediaDocument.user_id)
            .order_by(desc(MediaDocument.created_at))
        )

        if status_filter == "active":
            stmt = stmt.where(MediaDocument.tombstoned_at.is_(None))
        elif status_filter == "tombstoned":
            stmt = stmt.where(MediaDocument.tombstoned_at.is_not(None))

        if query and query.strip():
            like_term = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    MediaDocument.title.ilike(like_term),
                    MediaDocument.url.ilike(like_term),
                    MediaDocument.channel_or_author.ilike(like_term),
                    UserIdentity.native_user_id.ilike(like_term),
                )
            )

        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()

        rows = (await session.execute(stmt.limit(limit).offset(offset))).all()

        items = [
            {
                "id": str(row[0]),
                "url": row[1],
                "media_type": row[2],
                "title": row[3],
                "channel_or_author": row[4],
                "duration_seconds": row[5],
                "summary": row[6],
                "key_takeaways": row[7] or [],
                "topics": row[8] or [],
                "total_segments": row[9],
                "has_embedding": row[10],
                "created_at": row[11].isoformat() if row[11] else None,
                "updated_at": row[12].isoformat() if row[12] else None,
                "tombstoned_at": row[13].isoformat() if row[13] else None,
                "status": "tombstoned" if row[13] is not None else "active",
                "native_user_id": row[14],
            }
            for row in rows
        ]

        return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.get("/media/{media_id}", dependencies=[Depends(require_admin_session)])
async def get_admin_media_detail(media_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Fetch complete media document summary and timestamped segment breakdown."""
    async with request.app.state.session_factory() as session:
        stmt = (
            select(MediaDocument, UserIdentity.native_user_id)
            .join(UserIdentity, UserIdentity.id == MediaDocument.user_id)
            .where(MediaDocument.id == media_id)
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="media document not found")

        doc, native_user_id = row

        seg_stmt = (
            select(MediaSegment)
            .where(MediaSegment.document_id == doc.id)
            .order_by(MediaSegment.segment_index.asc())
        )
        segments = list((await session.execute(seg_stmt)).scalars().all())

        seg_items = [
            {
                "id": str(s.id),
                "segment_index": s.segment_index,
                "start_time_seconds": s.start_time_seconds,
                "end_time_seconds": s.end_time_seconds,
                "label": s.label,
                "content": s.content,
                "has_embedding": s.embedding is not None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in segments
        ]

        return {
            "id": str(doc.id),
            "url": doc.url,
            "media_type": doc.media_type,
            "title": doc.title,
            "description": doc.description,
            "channel_or_author": doc.channel_or_author,
            "duration_seconds": doc.duration_seconds,
            "summary": doc.summary,
            "key_takeaways": doc.key_takeaways or [],
            "topics": doc.topics or [],
            "total_segments": doc.total_segments,
            "has_embedding": doc.embedding is not None,
            "status": "tombstoned" if doc.tombstoned_at is not None else "active",
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            "tombstoned_at": doc.tombstoned_at.isoformat() if doc.tombstoned_at else None,
            "native_user_id": native_user_id,
            "segments": seg_items,
        }


@router.post("/media/index-url", dependencies=[Depends(require_admin_session)])
async def index_admin_media_url(
    body: IndexMediaUrlRequest, request: Request
) -> dict[str, Any]:
    """Analyze and persist a media URL on demand."""
    async with request.app.state.session_factory() as session:
        user_res = (
            await session.execute(
                select(UserIdentity.id).where(UserIdentity.native_user_id == body.native_user_id)
            )
        ).scalar_one_or_none()
        if user_res is None:
            user_id = uuid.uuid4()
            session.add(UserIdentity(id=user_id, native_user_id=body.native_user_id))
            await session.flush()
        else:
            user_id = user_res

        analyzer = get_media_analyzer(request.app.state.settings)
        analysis = await analyzer.analyze_media(url=body.url, media_type=body.media_type)

        embedder = None
        try:
            embedder = get_conversation_embedder(request.app.state.settings)
        except Exception:  # noqa: BLE001
            embedder = None

        doc_emb: list[float] | None = None
        seg_embs: list[list[float] | None] | None = None
        if embedder is not None:
            try:
                doc_emb = await asyncio.to_thread(
                    embedder.embed_one, f"{analysis.title}\n{analysis.summary}"
                )
            except Exception:  # noqa: BLE001
                doc_emb = None

            if analysis.segments:
                seg_texts = [f"{s.label or ''}\n{s.content}" for s in analysis.segments]
                try:
                    seg_embs = [
                        await asyncio.to_thread(embedder.embed_one, t) for t in seg_texts
                    ]
                except Exception:  # noqa: BLE001
                    seg_embs = None

        doc = await store_media_analysis(
            session,
            user_id=user_id,
            analysis=analysis,
            document_embedding=doc_emb,
            segment_embeddings=seg_embs,
        )
        await session.commit()

        return {
            "status": "indexed",
            "id": str(doc.id),
            "url": doc.url,
            "title": doc.title,
            "total_segments": doc.total_segments,
            "duration_seconds": doc.duration_seconds,
            "summary": doc.summary,
            "key_takeaways": doc.key_takeaways or [],
            "topics": doc.topics or [],
        }


@router.delete("/media/{media_id}", dependencies=[Depends(require_admin_session)])
async def delete_admin_media(
    media_id: uuid.UUID,
    request: Request,
    permanent: bool = False,
) -> dict[str, Any]:
    """Tombstone or permanently delete a media document."""
    async with request.app.state.session_factory() as session:
        if permanent:
            success = await delete_media_document(session, media_id)
        else:
            success = await tombstone_media_document(session, media_id)

        if not success:
            raise HTTPException(status_code=404, detail="media document not found")

        await session.commit()
        return {"status": "deleted" if permanent else "tombstoned", "id": str(media_id)}

@router.post("/playground/search", dependencies=[Depends(require_admin_session)])
async def playground_search(body: PlaygroundSearchRequest, request: Request) -> dict[str, Any]:
    """Execute hybrid or filtered search for dashboard inspection and prompt preview."""
    started_at = perf_counter()
    query_embedding = await _embed_query(request, body.query)
    mode = "hybrid" if query_embedding is not None else "lexical"

    async with request.app.state.session_factory() as session:
        memory_records = []
        if body.source_type in ("all", "memories"):
            memory_records = await search_explicit_memory(
                session,
                native_user_id=body.native_user_id,
                query=body.query,
                limit=body.limit,
            )

        conversation_hits = []
        if body.source_type in ("all", "conversations"):
            conversation_hits = await search_conversation_context(
                session,
                native_user_id=body.native_user_id,
                query=body.query,
                query_embedding=query_embedding,
                limit=body.limit,
            )

        file_hits = []
        if body.source_type in ("all", "files"):
            file_hits = await search_file_passages(
                session,
                native_user_id=body.native_user_id,
                query_text=body.query,
                query_embedding=query_embedding,
                limit=body.limit,
            )
        episode_hits = []
        if body.source_type in ("all", "episodes"):
            episode_hits = await search_topic_episodes(
                session,
                native_user_id=body.native_user_id,
                query_text=body.query,
                query_embedding=query_embedding,
                limit=body.limit,
            )
        media_hits = []
        if body.source_type in ("all", "media"):
            media_hits = await search_media_segments(
                session,
                native_user_id=body.native_user_id,
                query_text=body.query,
                query_embedding=query_embedding,
                limit=body.limit,
            )

        profile_snapshot = await get_or_create_profile(
            session,
            native_user_id=body.native_user_id,
            native_chat_id="playground-preview",
        )

    ranked: list[dict[str, Any]] = []
    rrf_k = 60

    for rank, m in enumerate(memory_records, start=1):
        rrf_score = 1.0 / (rrf_k + rank)
        ranked.append(
            {
                "source_id": str(m.id),
                "source_type": "memory",
                "category": m.category,
                "statement": m.statement,
                "preview": m.statement[:240] + ("…" if len(m.statement) > 240 else ""),
                "rrf_score": round(rrf_score, 6),
                "lexical_rank": rank,
                "vector_score": None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "metadata": {"key": m.key, "kind": m.kind},
            }
        )

    for h in conversation_hits:
        ranked.append(
            {
                "source_id": str(h.source_id),
                "source_type": "conversation",
                "category": "conversation_turn",
                "role": h.role,
                "statement": h.content,
                "preview": h.content[:240] + ("…" if len(h.content) > 240 else ""),
                "rrf_score": round(h.score, 6),
                "lexical_rank": None,
                "vector_score": None,
                "created_at": h.occurred_at.isoformat() if h.occurred_at else None,
                "metadata": {
                    "native_chat_id": h.native_chat_id,
                    "native_message_id": h.native_message_id,
                },
            }
        )

    for f in file_hits:
        ranked.append(
            {
                "source_id": str(f.reference_id),
                "source_type": "file",
                "category": "file_passage",
                "statement": f.content,
                "preview": f.content[:240] + ("…" if len(f.content) > 240 else ""),
                "rrf_score": round(f.score, 6),
                "lexical_rank": f.lexical_rank,
                "vector_score": f.semantic_rank,
                "created_at": None,
                "metadata": {
                    "filename": f.filename,
                    "native_file_id": f.native_file_id,
                    "chunk_ordinal": f.chunk_ordinal,
                    "header_path": f.header_path,
                },
            }
        )
    for ep in episode_hits:
        ranked.append(
            {
                "source_id": str(ep.episode_id),
                "source_type": "episode",
                "category": ep.topic_category,
                "statement": ep.summary,
                "preview": f"[{ep.title}] {ep.summary[:200]}...",
                "rrf_score": round(ep.score, 6),
                "lexical_rank": None,
                "vector_score": None,
                "created_at": ep.created_at.isoformat() if ep.created_at else None,
                "metadata": {
                    "title": ep.title,
                    "native_chat_id": ep.native_chat_id,
                    "decisions_made": ep.decisions_made,
                    "open_loops": ep.open_loops,
                    "key_entities": ep.key_entities,
                    "turn_count": ep.turn_count,
                },
            }
        )
    for med in media_hits:
        start_sec = med.start_time_seconds or 0
        end_sec = med.end_time_seconds or 0
        m_min, m_sec = divmod(start_sec, 60)
        e_min, e_sec = divmod(end_sec, 60)
        time_str = f"[{m_min:02d}:{m_sec:02d} - {e_min:02d}:{e_sec:02d}]"
        label_str = f" ({med.label})" if med.label else ""
        preview_text = f"[{med.title} {time_str}{label_str}] {med.content[:200]}"
        ranked.append(
            {
                "source_id": str(med.segment_id or med.document_id),
                "source_type": "media",
                "category": "media_segment",
                "statement": med.content,
                "preview": preview_text,
                "rrf_score": round(med.score, 6),
                "lexical_rank": None,
                "vector_score": None,
                "created_at": med.created_at.isoformat() if med.created_at else None,
                "metadata": {
                    "document_id": str(med.document_id),
                    "url": med.url,
                    "media_type": med.media_type,
                    "title": med.title,
                    "channel_or_author": med.channel_or_author,
                    "start_time_seconds": med.start_time_seconds,
                    "end_time_seconds": med.end_time_seconds,
                    "label": med.label,
                    "match_mode": med.match_mode,
                },
            }
        )

    ranked.sort(key=lambda item: -item["rrf_score"])
    selected = ranked[: body.limit]
    duration_ms = round((perf_counter() - started_at) * 1000, 2)

    context_lines = []
    if profile_snapshot and profile_snapshot.rendered_text:
        context_lines.append(profile_snapshot.rendered_text)
    if selected:
        context_lines.append("\n<retrieved_context>")
        for idx, item in enumerate(selected, start=1):
            if item["source_type"] == "memory":
                context_lines.append(f"- [Memory #{idx}] ({item['category']}): {item['statement']}")
            elif item["source_type"] == "file":
                meta = item["metadata"]
                context_lines.append(
                    f"- [Document #{idx}] ({meta.get('filename')}, chunk {meta.get('chunk_ordinal')}): {item['statement']}"
                )
            elif item["source_type"] == "conversation":
                context_lines.append(
                    f"- [Prior Turn #{idx}] ({item.get('role', 'user')}): {item['statement']}"
                )
            elif item["source_type"] == "episode":
                meta = item["metadata"]
                dec_str = (
                    f" | Decisions: {'; '.join(meta.get('decisions_made', []))}"
                    if meta.get("decisions_made")
                    else ""
                )
                context_lines.append(
                    f"- [Topic Episode #{idx}] ({meta.get('title')}, {item['category']}): {item['statement']}{dec_str}"
                )
            elif item["source_type"] == "media":
                meta = item["metadata"]
                s_sec = meta.get("start_time_seconds")
                e_sec = meta.get("end_time_seconds")
                time_s = f" ({s_sec}s - {e_sec}s)" if s_sec is not None else ""
                context_lines.append(
                    f"- [Media Segment #{idx}] ({meta.get('title')}{time_s}): {item['statement']}"
                )
        context_lines.append("</retrieved_context>")

    rendered_llm_block = "\n".join(context_lines)

    return {
        "mode": mode,
        "duration_ms": duration_ms,
        "query": body.query,
        "native_user_id": body.native_user_id,
        "total_results": len(selected),
        "results": selected,
        "profile_rendered": profile_snapshot.rendered_text if profile_snapshot else "",
        "rendered_llm_block": rendered_llm_block,
    }
