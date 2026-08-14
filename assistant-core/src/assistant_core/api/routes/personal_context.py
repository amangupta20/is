"""Signed hybrid personal-memory and conversation-context routes."""

import asyncio
import uuid
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from assistant_core.api.dependencies import (
    require_adapter_signature,
    require_session_or_signature,
)
from assistant_core.conversation.embedder import (
    ConversationEmbeddingError,
    EmbeddingConfigurationError,
    get_conversation_embedder,
)
from assistant_core.conversation.repository import (
    read_conversation_context,
    search_conversation_context,
)
from assistant_core.files.repository import search_file_context
from assistant_core.identity.models import UserIdentity
from assistant_core.memory.repository import (
    archive_user_memory,
    list_user_memories,
    merge_user_memories,
    read_explicit_memory,
    search_explicit_memory,
    update_user_memory,
)

router = APIRouter(prefix="/v1/personal-context", tags=["personal-context"])
LOGGER = structlog.get_logger("assistant_core.personal_context")
RRF_K = 60


def _compact_preview(statement: str) -> str:
    """Normalize a statement and cap its preview at 240 characters."""
    normalized = " ".join(statement.split())
    return normalized if len(normalized) <= 240 else normalized[:239] + "…"


class PersonalContextSearchRequest(BaseModel):
    """Bounded native identity and hybrid search request."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    native_chat_id: str | None = Field(default=None, min_length=1, max_length=200)
    native_message_id: str | None = Field(default=None, min_length=1, max_length=200)
    query: str
    limit: int = Field(default=5, ge=1, le=10)

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value: object) -> object:
        """Strip surrounding whitespace before enforcing query bounds."""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        if len(stripped) > 1_000:
            raise ValueError("query must not exceed 1000 characters")
        return stripped


class PersonalContextPreview(BaseModel):
    """One opaque source ID and compact provenance-aware preview."""

    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    source_type: Literal["memory", "conversation", "file"]
    category: str
    role: Literal["user", "assistant"] | None
    preview: str
    source_native_chat_id: str | None
    source_native_message_id: str | None


class PersonalContextSearchResponse(BaseModel):
    """Bounded personal-context results with declared retrieval mode."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["lexical", "hybrid"]
    results: list[PersonalContextPreview]


class PersonalContextReadRequest(BaseModel):
    """Native identity and one opaque personal-context source ID."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    memory_source_id: uuid.UUID


class PersonalContextNeighbor(BaseModel):
    """One bounded adjacent conversation passage."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str
    source_native_chat_id: str
    source_native_message_id: str


class PersonalContextReadResponse(BaseModel):
    """One source-linked memory or conversation passage."""

    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    source_type: Literal["memory", "conversation"]
    content: str
    category: str
    role: Literal["user", "assistant"] | None
    evidence_quote: str | None
    source_native_chat_id: str
    source_native_message_id: str
    neighbors: list[PersonalContextNeighbor]
    full_source_available: bool


async def _embed_query(request: Request, query: str) -> list[float] | None:
    """Embed a query outside a database transaction, failing open to lexical mode."""
    try:
        embedder = get_conversation_embedder(request.app.state.settings)
        return await asyncio.to_thread(embedder.embed_one, query)
    except (EmbeddingConfigurationError, ConversationEmbeddingError):
        return None


@router.post(
    "/search",
    dependencies=[Depends(require_adapter_signature)],
    response_model=PersonalContextSearchResponse,
)
async def search_personal_context(
    body: PersonalContextSearchRequest, request: Request
) -> PersonalContextSearchResponse:
    """Search active explicit memory and owner-scoped conversation/file evidence."""
    started_at = perf_counter()
    query_embedding = await _embed_query(request, body.query)
    mode: Literal["lexical", "hybrid"] = (
        "hybrid" if query_embedding is not None else "lexical"
    )
    async with request.app.state.session_factory() as session:
        memory_records = await search_explicit_memory(
            session,
            native_user_id=body.native_user_id,
            query=body.query,
            limit=body.limit,
        )
        conversation_hits = await search_conversation_context(
            session,
            native_user_id=body.native_user_id,
            query=body.query,
            query_embedding=query_embedding,
            limit=body.limit,
        )
        file_hits: list[dict[str, object]] = []
        try:
            from sqlalchemy import select

            user_id = (
                await session.execute(
                    select(UserIdentity.id).where(UserIdentity.native_user_id == body.native_user_id)
                )
            ).scalar_one_or_none()
            if user_id is not None:
                file_hits = await search_file_context(
                    session,
                    user_id=user_id,
                    query=body.query,
                    query_embedding=query_embedding,
                    limit=body.limit,
                )
        except Exception:  # noqa: BLE001 - file search must not break main search
            file_hits = []

    ranked: list[tuple[float, int, str, PersonalContextPreview]] = []
    for rank, record in enumerate(memory_records, start=1):
        preview = PersonalContextPreview(
            source_id=record.id,
            source_type="memory",
            category=record.category,
            role=None,
            preview=_compact_preview(record.statement),
            source_native_chat_id=None,
            source_native_message_id=None,
        )
        ranked.append((1.0 / (RRF_K + rank), 0, str(record.id), preview))
    for hit in conversation_hits:
        preview = PersonalContextPreview(
            source_id=hit.source_id,
            source_type="conversation",
            category="conversation_evidence",
            role=hit.role,
            preview=_compact_preview(hit.content),
            source_native_chat_id=hit.native_chat_id,
            source_native_message_id=hit.native_message_id,
        )
        ranked.append((hit.score, 1, str(hit.source_id), preview))
    for file_hit in file_hits:
        preview = PersonalContextPreview(
            source_id=file_hit["source_id"],  # type: ignore[arg-type]
            source_type="file",
            category="file_evidence",
            role=None,
            preview=_compact_preview(str(file_hit["content"])),
            source_native_chat_id=str(file_hit["native_file_id"]),
            source_native_message_id=str(file_hit["chunk_ordinal"]),
        )
        ranked.append((float(file_hit["score"]), 1, str(file_hit["source_id"]), preview))  # type: ignore[arg-type]
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = ranked[: body.limit]
    LOGGER.info(
        "personal_context_search_completed",
        native_user_id=body.native_user_id,
        native_chat_id=body.native_chat_id,
        native_message_id=body.native_message_id,
        mode=mode,
        memory_result_count=len(memory_records),
        conversation_result_count=len(conversation_hits),
        file_result_count=len(file_hits),
        result_count=len(selected),
        top_score=round(selected[0][0], 6) if selected else None,
        duration_ms=round((perf_counter() - started_at) * 1000, 3),
    )
    return PersonalContextSearchResponse(
        mode=mode,
        results=[item[3] for item in selected],
    )


@router.post(
    "/read",
    dependencies=[Depends(require_adapter_signature)],
    response_model=PersonalContextReadResponse,
)
async def read_personal_context(
    body: PersonalContextReadRequest, request: Request
) -> PersonalContextReadResponse:
    """Read one owned memory first, otherwise one bounded conversation source."""
    async with request.app.state.session_factory() as session:
        memory_result = await read_explicit_memory(
            session,
            native_user_id=body.native_user_id,
            memory_source_id=body.memory_source_id,
        )
        if memory_result is not None:
            record, evidence, turn = memory_result
            response = PersonalContextReadResponse(
                source_id=record.id,
                source_type="memory",
                content=record.statement,
                category=record.category,
                role=None,
                evidence_quote=evidence.evidence_quote,
                source_native_chat_id=turn.native_chat_id,
                source_native_message_id=turn.native_user_message_id,
                neighbors=[],
                full_source_available=False,
            )
        else:
            conversation_result = await read_conversation_context(
                session,
                native_user_id=body.native_user_id,
                source_id=body.memory_source_id,
            )
            if conversation_result is None:
                raise HTTPException(
                    status_code=404, detail="memory source not found"
                )
            selected = conversation_result.selected
            response = PersonalContextReadResponse(
                source_id=selected.source_id,
                source_type="conversation",
                content=selected.content,
                category="conversation_evidence",
                role=selected.role,
                evidence_quote=None,
                source_native_chat_id=selected.native_chat_id,
                source_native_message_id=selected.native_message_id,
                neighbors=[
                    PersonalContextNeighbor(
                        role=neighbor.role,
                        content=neighbor.content,
                        source_native_chat_id=neighbor.native_chat_id,
                        source_native_message_id=neighbor.native_message_id,
                    )
                    for neighbor in conversation_result.neighbors
                ],
                full_source_available=False,
            )
    LOGGER.info(
        "personal_context_read_completed",
        native_user_id=body.native_user_id,
        source_id=str(response.source_id),
        source_type=response.source_type,
        role=response.role,
        source_native_chat_id=response.source_native_chat_id,
        source_native_message_id=response.source_native_message_id,
        neighbor_count=len(response.neighbors),
        content_chars=len(response.content),
    )
    return response


class PersonalContextListRequest(BaseModel):
    """Filter parameters for listing a user's stored memories."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    status: str = Field(default="active", min_length=1, max_length=30)
    category: str | None = Field(default=None, max_length=30)
    limit: int = Field(default=100, ge=1, le=500)


class UserMemoryItemResponse(BaseModel):
    """Detailed memory item representation."""

    model_config = ConfigDict(extra="forbid")

    id: str
    key: str
    category: str
    statement: str
    state: str
    status: str | None = None
    confidence: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    superseded_at: datetime | None = None
    superseded_by_id: str | None = None
    evidence_quote: str | None = None
    native_chat_id: str | None = None
    native_message_id: str | None = None


class PersonalContextListResponse(BaseModel):
    """List of detailed user memories."""

    model_config = ConfigDict(extra="forbid")

    memories: list[UserMemoryItemResponse]


class PersonalContextArchiveRequest(BaseModel):
    """Request to archive one owned memory record."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    memory_id: uuid.UUID


class PersonalContextArchiveResponse(BaseModel):
    """Confirmation of archived memory."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    archived_id: str
    archived_at: str


class PersonalContextUpdateRequest(BaseModel):
    """Request to update statement of one owned memory record."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    memory_id: uuid.UUID
    new_statement: str = Field(min_length=1, max_length=2000)

    @field_validator("new_statement", mode="before")
    @classmethod
    def strip_statement(cls, value: object) -> object:
        """Strip surrounding whitespace before enforcing statement bounds."""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("statement must not be blank")
        return stripped


class PersonalContextUpdateResponse(BaseModel):
    """Confirmation of updated memory statement."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    memory_id: str
    statement: str


class PersonalContextMergeRequest(BaseModel):
    """Request to consolidate multiple memories into one new record."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    source_memory_ids: list[uuid.UUID] = Field(min_length=2, max_length=50)
    target_category: str = Field(default="preference", max_length=30)
    new_statement: str = Field(min_length=1, max_length=2000)

    @field_validator("new_statement", mode="before")
    @classmethod
    def strip_statement(cls, value: object) -> object:
        """Strip surrounding whitespace before enforcing statement bounds."""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("statement must not be blank")
        return stripped


class PersonalContextMergeResponse(BaseModel):
    """Confirmation of merged memory creation and source archival."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    created_id: str
    archived_source_ids: list[str]


@router.post(
    "/list",
    dependencies=[Depends(require_session_or_signature)],
    response_model=PersonalContextListResponse,
)
async def list_personal_context_memories(
    body: PersonalContextListRequest, request: Request
) -> PersonalContextListResponse:
    """List owned memories with optional status and category filters."""
    async with request.app.state.session_factory() as session:
        items = await list_user_memories(
            session,
            native_user_id=body.native_user_id,
            status=body.status,
            category=body.category,
            limit=body.limit,
        )
    LOGGER.info(
        "personal_context_memories_listed",
        native_user_id=body.native_user_id,
        status=body.status,
        category=body.category,
        count=len(items),
    )
    return PersonalContextListResponse(
        memories=[
            UserMemoryItemResponse(
                id=item.id,
                key=item.key,
                category=item.category,
                statement=item.statement,
                state=item.state,
                status=item.state,
                confidence=item.confidence,
                created_at=item.created_at,
                updated_at=item.updated_at,
                archived_at=item.archived_at,
                superseded_at=item.superseded_at,
                superseded_by_id=item.superseded_by_id,
                evidence_quote=item.evidence_quote,
                native_chat_id=item.native_chat_id,
                native_message_id=item.native_message_id,
            )
            for item in items
        ]
    )


@router.post(
    "/archive",
    dependencies=[Depends(require_session_or_signature)],
    response_model=PersonalContextArchiveResponse,
)
async def archive_personal_context_memory(
    body: PersonalContextArchiveRequest, request: Request
) -> PersonalContextArchiveResponse:
    """Archive an owned memory record."""
    async with request.app.state.session_factory() as session:
        record = await archive_user_memory(
            session,
            native_user_id=body.native_user_id,
            memory_id=body.memory_id,
        )
        if record is None:
            raise HTTPException(
                status_code=404, detail="memory record not found"
            )
        await session.commit()
    archived_at_str = (
        record.archived_at.isoformat()
        if record.archived_at
        else datetime.now(UTC).isoformat()
    )
    LOGGER.info(
        "personal_context_memory_archived",
        native_user_id=body.native_user_id,
        memory_id=str(record.id),
    )
    return PersonalContextArchiveResponse(
        status="ok",
        archived_id=str(record.id),
        archived_at=archived_at_str,
    )


@router.post(
    "/update",
    dependencies=[Depends(require_session_or_signature)],
    response_model=PersonalContextUpdateResponse,
)
async def update_personal_context_memory(
    body: PersonalContextUpdateRequest, request: Request
) -> PersonalContextUpdateResponse:
    """Update statement of an owned memory record."""
    async with request.app.state.session_factory() as session:
        record = await update_user_memory(
            session,
            native_user_id=body.native_user_id,
            memory_id=body.memory_id,
            new_statement=body.new_statement,
        )
        if record is None:
            raise HTTPException(
                status_code=404, detail="memory record not found"
            )
        await session.commit()
    LOGGER.info(
        "personal_context_memory_updated",
        native_user_id=body.native_user_id,
        memory_id=str(record.id),
    )
    return PersonalContextUpdateResponse(
        status="ok",
        memory_id=str(record.id),
        statement=record.statement,
    )


@router.post(
    "/merge",
    dependencies=[Depends(require_session_or_signature)],
    response_model=PersonalContextMergeResponse,
)
async def merge_personal_context_memories(
    body: PersonalContextMergeRequest, request: Request
) -> PersonalContextMergeResponse:
    """Consolidate multiple owned memories into a new record and archive sources."""
    async with request.app.state.session_factory() as session:
        result = await merge_user_memories(
            session,
            native_user_id=body.native_user_id,
            source_memory_ids=body.source_memory_ids,
            target_category=body.target_category,
            new_statement=body.new_statement,
        )
        if result is None:
            raise HTTPException(
                status_code=404, detail="one or more source memories not found"
            )
        new_record, source_ids = result
        await session.commit()
    LOGGER.info(
        "personal_context_memories_merged",
        native_user_id=body.native_user_id,
        created_id=str(new_record.id),
        archived_source_ids=[str(sid) for sid in source_ids],
    )
    return PersonalContextMergeResponse(
        status="ok",
        created_id=str(new_record.id),
        archived_source_ids=[str(sid) for sid in source_ids],
    )

