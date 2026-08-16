import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import desc, func, or_, select, update

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
from assistant_core.events.models import EventInbox
from assistant_core.files.models import FileDocument, FileReference, FileSegment
from assistant_core.files.repository import search_file_passages, tombstone_file_references
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.jobs.worker import (
    TaskModelConfigurationError,
    get_memory_consolidator,
)
from assistant_core.memory.consolidator import MemoryConsolidationError
from assistant_core.memory.models import ChatProfileSnapshot, MemoryEvidence, MemoryRecord
from assistant_core.memory.profile import get_or_create_profile
from assistant_core.memory.repository import (
    consolidate_user_memories,
    search_explicit_memory,
)
from assistant_core.turns.models import CompletedTurn

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
    source_type: Literal["all", "memories", "files", "conversations"] = "all"


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


class UpdateMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str | None = Field(default=None, min_length=1, max_length=2000)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    state: Literal["active", "tombstoned"] | None = None


class BatchDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


class BatchDeleteFilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    native_file_ids: list[str] = Field(min_length=1, max_length=1000)


class PurgeSystemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str
    scope: Literal["all", "memories", "files", "conversations", "jobs"] = "all"


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
            "conversations": {
                "active_turns": total_turns,
                "indexed_passages": total_conv_segments,
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


@router.get("/memories", dependencies=[Depends(require_admin_session)])
async def list_memories(
    request: Request,
    query: str | None = None,
    category: str | None = None,
    status_filter: Literal["active", "archived", "all"] = "active",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Search and paginate memories with category and status filters."""
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
            )
            .join(UserIdentity, UserIdentity.id == MemoryRecord.user_id)
            .order_by(desc(MemoryRecord.created_at))
        )

        if status_filter == "active":
            stmt = stmt.where(MemoryRecord.state == "active")
        elif status_filter == "archived":
            stmt = stmt.where(MemoryRecord.state != "active")

        if category:
            stmt = stmt.where(MemoryRecord.category == category)

        if query and query.strip():
            like_term = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    MemoryRecord.statement.ilike(like_term),
                    MemoryRecord.category.ilike(like_term),
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
                "statement": row[1],
                "category": row[2],
                "confidence": row[3],
                "state": row[4],
                "created_at": row[5].isoformat() if row[5] else None,
                "archived_at": row[6].isoformat() if row[6] else None,
                "native_user_id": row[7],
            }
            for row in rows
        ]

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

        session.add(
            MemoryRecord(
                id=record_id,
                user_id=user_id,
                key=key,
                kind="explicit",
                category=category,
                statement=body.statement,
                confidence=1,
                state="active",
                created_at=now,
            )
        )
        session.add(
            MemoryEvidence(
                id=uuid.uuid4(),
                memory_record_id=record_id,
                completed_turn_id=turn_id,
                native_user_message_id="admin-manual",
                evidence_quote=body.evidence_quote or body.statement,
            )
        )
        await session.commit()

        return {
            "id": str(record_id),
            "native_user_id": body.native_user_id,
            "statement": body.statement,
            "category": category,
            "state": "active",
            "created_at": now.isoformat(),
        }


@router.patch("/memories/{memory_id}", dependencies=[Depends(require_admin_session)])
async def update_memory(
    memory_id: uuid.UUID, body: UpdateMemoryRequest, request: Request
) -> dict[str, Any]:
    """Update memory statement, category, or active state."""
    async with request.app.state.session_factory() as session:
        record = (
            await session.execute(select(MemoryRecord).where(MemoryRecord.id == memory_id))
        ).scalar_one_or_none()

        if record is None:
            raise HTTPException(status_code=404, detail="memory not found")

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
        if body.state is not None:
            if body.state == "tombstoned":
                record.state = "archived"
                record.archived_at = datetime.now(UTC)
            elif body.state == "active":
                record.state = "active"
                record.archived_at = None

        await session.commit()
        return {
            "id": str(record.id),
            "statement": record.statement,
            "category": record.category,
            "state": record.state,
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

        record.state = "archived"
        record.archived_at = datetime.now(UTC)
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

        if scope in ("jobs", "all"):
            job_count = (await session.execute(sa_delete(Job))).rowcount or 0
            inbox_count = (await session.execute(sa_delete(EventInbox))).rowcount or 0
            deleted_counts["jobs"] = job_count
            deleted_counts["events"] = inbox_count

        await session.commit()

    return {
        "status": "purged",
        "scope": scope,
        "deleted": deleted_counts,
    }


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
                    session, native_user_id=u, consolidator=consolidator
                )
                applied_all.extend(applied)

            await session.commit()
    except MemoryConsolidationError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Memory consolidation failed during LLM evaluation: {exc}",
        ) from None

    return {
        "status": "success",
        "consolidated_users": len(users),
        "total_superseded": len(applied_all),
        "details": applied_all,
    }


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
