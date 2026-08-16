"""Management and observability admin routes."""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, or_, select

from assistant_core.auth.admin import (
    DEFAULT_SESSION_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    create_admin_session_token,
    require_admin_session,
    verify_admin_credentials,
)
from assistant_core.conversation.models import ConversationSegment
from assistant_core.files.models import FileDocument, FileReference, FileSegment
from assistant_core.files.repository import tombstone_file_references
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.memory.models import MemoryEvidence, MemoryRecord
from assistant_core.turns.models import CompletedTurn

router = APIRouter(prefix="/v1/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------


class AdminLoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=500)


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
