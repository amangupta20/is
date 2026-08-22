"""Signed hybrid personal-memory, conversation-context, and file-passage routes."""

import asyncio
import uuid
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.conversation.embedder import (
    ConversationEmbeddingError,
    EmbeddingConfigurationError,
    get_conversation_embedder,
)
from assistant_core.conversation.repository import (
    read_conversation_context,
    search_conversation_context,
)
from assistant_core.episodes.repository import (
    get_topic_episode,
    search_topic_episodes,
)
from assistant_core.files.repository import (
    get_full_file_content,
    read_file_passage_context,
    search_file_passages,
)
from assistant_core.memory.repository import (
    forget_direct_memory,
    list_user_memories,
    read_explicit_memory,
    save_direct_memory,
    search_explicit_memory,
    update_direct_memory,
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
    native_project_id: str | None = Field(default=None, min_length=1, max_length=200)
    native_folder_id: str | None = Field(default=None, min_length=1, max_length=200)
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
    source_type: Literal["memory", "conversation", "file", "episode"]
    category: str
    role: Literal["user", "assistant"] | None
    preview: str
    source_native_chat_id: str | None
    source_native_message_id: str | None
    source_native_project_id: str | None = None
    source_native_folder_id: str | None = None
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    temporal_tag: str | None = None
    filename: str | None = None
    native_file_id: str | None = None
    header_path: str | None = None
    chunk_ordinal: int | None = None
    title: str | None = None
    decisions_made: list[str] | None = None
    open_loops: list[str] | None = None
    key_entities: list[str] | None = None
    turn_count: int | None = None


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


class PersonalContextFileRequest(BaseModel):
    """Native identity and file identifier or filename."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    file_id_or_name: str = Field(min_length=1, max_length=500)


class PersonalContextFileResponse(BaseModel):
    """Full reconstructed document content."""

    model_config = ConfigDict(extra="forbid")

    native_file_id: str
    filename: str
    mime_type: str
    total_chunks: int
    total_characters: int
    content: str


class PersonalContextNeighbor(BaseModel):
    """One bounded adjacent conversation passage."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str
    source_native_chat_id: str
    source_native_message_id: str
    source_native_project_id: str | None = None
    source_native_folder_id: str | None = None


class PersonalContextReadResponse(BaseModel):
    """One source-linked memory, conversation passage, or file chunk."""

    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    source_type: Literal["memory", "conversation", "file", "episode"]
    content: str
    category: str
    role: Literal["user", "assistant"] | None
    evidence_quote: str | None
    source_native_chat_id: str | None
    source_native_message_id: str | None
    source_native_project_id: str | None = None
    source_native_folder_id: str | None = None
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    temporal_tag: str | None = None
    filename: str | None = None
    native_file_id: str | None = None
    header_path: str | None = None
    chunk_ordinal: int | None = None
    previous_chunk: str | None = None
    next_chunk: str | None = None
    neighbors: list[PersonalContextNeighbor] = Field(default_factory=list)
    title: str | None = None
    decisions_made: list[str] | None = None
    open_loops: list[str] | None = None
    key_entities: list[str] | None = None
    turn_count: int | None = None
    full_source_available: bool = False


class MemoryItemResponse(BaseModel):
    """Normalized active memory representation for tool inspection."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    key: str
    category: str
    statement: str
    temporal_tag: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemoryListRequest(BaseModel):
    """Direct in-chat memory retrieval query."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    query: str | None = None
    category: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class MemoryListResponse(BaseModel):
    """Active memory items matching query."""

    model_config = ConfigDict(extra="forbid")

    memories: list[MemoryItemResponse]
    total: int


class MemorySaveRequest(BaseModel):
    """Direct in-chat explicit memory creation or replacement."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    key: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="fact", min_length=2, max_length=50)
    temporal_tag: str | None = Field(default=None, max_length=50)
    expires_at: datetime | None = None


class MemorySaveResponse(BaseModel):
    """Outcome of direct memory creation."""

    model_config = ConfigDict(extra="forbid")

    memory: MemoryItemResponse
    created: bool
    message: str


class MemoryUpdateRequest(BaseModel):
    """Direct in-chat memory update."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    memory_id: uuid.UUID
    statement: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=50)
    temporal_tag: str | None = Field(default=None, max_length=50)
    expires_at: datetime | None = None
    clear_expiration: bool = False


class MemoryUpdateResponse(BaseModel):
    """Outcome of direct memory modification."""

    model_config = ConfigDict(extra="forbid")

    memory: MemoryItemResponse
    message: str


class MemoryForgetRequest(BaseModel):
    """Direct in-chat memory invalidation / archive request."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    memory_id: uuid.UUID | None = None
    key: str | None = None
    reason: str | None = None


class MemoryForgetResponse(BaseModel):
    """Outcome of direct memory removal."""

    model_config = ConfigDict(extra="forbid")

    archived_count: int
    archived_ids: list[uuid.UUID]
    message: str


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
    """Search active explicit memory, conversation evidence, and file passages."""
    started_at = perf_counter()
    query_embedding = await _embed_query(request, body.query)
    mode: Literal["lexical", "hybrid"] = "hybrid" if query_embedding is not None else "lexical"
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
        file_hits = await search_file_passages(
            session,
            native_user_id=body.native_user_id,
            query_text=body.query,
            query_embedding=query_embedding,
            limit=body.limit,
        )
        episode_hits = await search_topic_episodes(
            session,
            native_user_id=body.native_user_id,
            query_text=body.query,
            query_embedding=query_embedding,
            limit=body.limit,
            native_project_id=body.native_project_id,
            native_folder_id=body.native_folder_id,
        )

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
            source_native_project_id=None,
            source_native_folder_id=None,
            valid_from=record.valid_from,
            expires_at=record.expires_at,
            temporal_tag=record.temporal_tag,
        )
        ranked.append((1.0 / (RRF_K + rank), 0, str(record.id), preview))

    target_chat_id = (body.native_chat_id or "").strip()
    target_project_id = (body.native_project_id or "").strip()
    target_folder_id = (body.native_folder_id or "").strip()

    for hit in conversation_hits:
        score = hit.score
        # Tier 2 Active Scope Boost: +0.15 for matching folder/project, +0.05 for current chat
        if (target_folder_id and hit.native_folder_id == target_folder_id) or (
            target_project_id and hit.native_project_id == target_project_id
        ):
            score += 0.15
        elif target_chat_id and hit.native_chat_id == target_chat_id:
            score += 0.05

        preview = PersonalContextPreview(
            source_id=hit.source_id,
            source_type="conversation",
            category="conversation_evidence",
            role=hit.role,
            preview=_compact_preview(hit.content),
            source_native_chat_id=hit.native_chat_id,
            source_native_message_id=hit.native_message_id,
            source_native_project_id=hit.native_project_id,
            source_native_folder_id=hit.native_folder_id,
        )
        ranked.append((score, 1, str(hit.source_id), preview))

    for fhit in file_hits:
        preview = PersonalContextPreview(
            source_id=uuid.UUID(fhit.reference_id),
            source_type="file",
            category="file_passage",
            role=None,
            preview=_compact_preview(fhit.content),
            source_native_chat_id=None,
            source_native_message_id=None,
            source_native_project_id=None,
            source_native_folder_id=None,
            filename=fhit.filename,
            native_file_id=fhit.native_file_id,
            header_path=fhit.header_path,
            chunk_ordinal=fhit.chunk_ordinal,
        )
        ranked.append((fhit.score, 2, fhit.reference_id, preview))
    for ehit in episode_hits:
        ep_score = ehit.score
        if (target_folder_id and ehit.native_folder_id == target_folder_id) or (
            target_project_id and ehit.native_project_id == target_project_id
        ):
            ep_score += 0.15
        elif target_chat_id and ehit.native_chat_id == target_chat_id:
            ep_score += 0.05

        preview_text = f"[{ehit.title}] {ehit.summary}"
        preview = PersonalContextPreview(
            source_id=ehit.episode_id,
            source_type="episode",
            category=ehit.topic_category,
            role=None,
            preview=_compact_preview(preview_text),
            source_native_chat_id=ehit.native_chat_id,
            source_native_message_id=ehit.start_message_id,
            source_native_project_id=ehit.native_project_id,
            source_native_folder_id=ehit.native_folder_id,
            title=ehit.title,
            decisions_made=ehit.decisions_made,
            open_loops=ehit.open_loops,
            key_entities=ehit.key_entities,
            turn_count=ehit.turn_count,
        )
        ranked.append((ep_score, 0, str(ehit.episode_id), preview))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = ranked[: body.limit]
    LOGGER.info(
        "personal_context_search_completed",
        native_user_id=body.native_user_id,
        native_chat_id=body.native_chat_id,
        native_project_id=body.native_project_id,
        native_folder_id=body.native_folder_id,
        native_message_id=body.native_message_id,
        mode=mode,
        memory_result_count=len(memory_records),
        conversation_result_count=len(conversation_hits),
        file_result_count=len(file_hits),
        episode_result_count=len(episode_hits),
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
    """Read one owned memory, conversation passage, or file passage."""
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
                source_native_project_id=turn.native_project_id,
                source_native_folder_id=turn.native_folder_id,
                valid_from=record.valid_from,
                expires_at=record.expires_at,
                temporal_tag=record.temporal_tag,
                neighbors=[],
                full_source_available=False,
            )
        elif (
            episode_result := await get_topic_episode(
                session,
                episode_id=body.memory_source_id,
                native_user_id=body.native_user_id,
            )
        ) is not None:
            content_lines = [
                f"# Topic Episode: {episode_result.title}",
                f"**Category:** {episode_result.topic_category} | **Turns:** {episode_result.turn_count}",
                f"\n## Summary\n{episode_result.summary}",
            ]
            if episode_result.decisions_made:
                content_lines.append("\n## Decisions Made")
                for dec in episode_result.decisions_made:
                    content_lines.append(f"- {dec}")
            if episode_result.open_loops:
                content_lines.append("\n## Open Loops / Pending Tasks")
                for loop in episode_result.open_loops:
                    content_lines.append(f"- {loop}")
            if episode_result.key_entities:
                content_lines.append(f"\n**Entities:** {', '.join(episode_result.key_entities)}")

            response = PersonalContextReadResponse(
                source_id=episode_result.id,
                source_type="episode",
                content="\n".join(content_lines),
                category=episode_result.topic_category,
                role=None,
                evidence_quote=None,
                source_native_chat_id=episode_result.native_chat_id,
                source_native_message_id=episode_result.start_message_id,
                source_native_project_id=episode_result.native_project_id,
                source_native_folder_id=episode_result.native_folder_id,
                title=episode_result.title,
                decisions_made=episode_result.decisions_made,
                open_loops=episode_result.open_loops,
                key_entities=episode_result.key_entities,
                turn_count=episode_result.turn_count,
                neighbors=[],
                full_source_available=True,
            )
        else:
            conversation_result = await read_conversation_context(
                session,
                native_user_id=body.native_user_id,
                source_id=body.memory_source_id,
            )
            if conversation_result is not None:
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
                    source_native_project_id=selected.native_project_id,
                    source_native_folder_id=selected.native_folder_id,
                    neighbors=[
                        PersonalContextNeighbor(
                            role=neighbor.role,
                            content=neighbor.content,
                            source_native_chat_id=neighbor.native_chat_id,
                            source_native_message_id=neighbor.native_message_id,
                            source_native_project_id=neighbor.native_project_id,
                            source_native_folder_id=neighbor.native_folder_id,
                        )
                        for neighbor in conversation_result.neighbors
                    ],
                    full_source_available=False,
                )
            else:
                file_result = await read_file_passage_context(
                    session,
                    native_user_id=body.native_user_id,
                    reference_id=body.memory_source_id,
                )
                if file_result is None:
                    raise HTTPException(status_code=404, detail="memory source not found")
                response = PersonalContextReadResponse(
                    source_id=uuid.UUID(file_result.reference_id),
                    source_type="file",
                    content=file_result.content,
                    category=file_result.mime_type,
                    role=None,
                    evidence_quote=None,
                    source_native_chat_id=None,
                    source_native_message_id=None,
                    filename=file_result.filename,
                    native_file_id=file_result.native_file_id,
                    header_path=file_result.header_path,
                    chunk_ordinal=file_result.chunk_ordinal,
                    previous_chunk=file_result.previous_content,
                    next_chunk=file_result.next_content,
                    neighbors=[],
                    full_source_available=True,
                )
    LOGGER.info(
        "personal_context_read_completed",
        native_user_id=body.native_user_id,
        source_id=str(response.source_id),
        source_type=response.source_type,
        role=response.role,
        source_native_chat_id=response.source_native_chat_id,
        source_native_message_id=response.source_native_message_id,
        filename=response.filename,
        neighbor_count=len(response.neighbors),
        content_chars=len(response.content),
    )
    return response


@router.post(
    "/file-content",
    dependencies=[Depends(require_adapter_signature)],
    response_model=PersonalContextFileResponse,
)
async def read_full_document(
    body: PersonalContextFileRequest, request: Request
) -> PersonalContextFileResponse:
    """Retrieve and reconstruct full document content in chunk order."""
    started_at = perf_counter()
    async with request.app.state.session_factory() as session:
        file_content = await get_full_file_content(
            session,
            native_user_id=body.native_user_id,
            file_id_or_name=body.file_id_or_name,
        )
        if file_content is None:
            raise HTTPException(status_code=404, detail="file not found")

    LOGGER.info(
        "personal_context_full_file_read_completed",
        native_user_id=body.native_user_id,
        native_file_id=file_content.native_file_id,
        filename=file_content.filename,
        total_chunks=file_content.total_chunks,
        total_characters=file_content.total_characters,
        duration_ms=round((perf_counter() - started_at) * 1000, 3),
    )
    return PersonalContextFileResponse(
        native_file_id=file_content.native_file_id,
        filename=file_content.filename,
        mime_type=file_content.mime_type,
        total_chunks=file_content.total_chunks,
        total_characters=file_content.total_characters,
        content=file_content.content,
    )


@router.post(
    "/memory/list",
    dependencies=[Depends(require_adapter_signature)],
    response_model=MemoryListResponse,
)
async def list_memories(
    body: MemoryListRequest, request: Request
) -> MemoryListResponse:
    """Retrieve active memories for the authenticated user."""
    async with request.app.state.session_factory() as session:
        records = await list_user_memories(
            session,
            native_user_id=body.native_user_id,
            query=body.query,
            category=body.category,
            status="active",
            limit=body.limit,
        )

    items = [
        MemoryItemResponse(
            id=r.id,
            key=r.key,
            category=r.category,
            statement=r.statement,
            temporal_tag=r.temporal_tag,
            expires_at=r.expires_at,
            created_at=r.created_at or datetime.now(UTC),
            updated_at=r.updated_at or datetime.now(UTC),
        )
        for r in records
    ]
    return MemoryListResponse(memories=items, total=len(items))


@router.post(
    "/memory/save",
    dependencies=[Depends(require_adapter_signature)],
    response_model=MemorySaveResponse,
)
async def save_memory(
    body: MemorySaveRequest, request: Request
) -> MemorySaveResponse:
    """Create or update an explicit memory directly via chat tool call."""
    async with request.app.state.session_factory() as session:
        record, created = await save_direct_memory(
            session,
            native_user_id=body.native_user_id,
            key=body.key,
            statement=body.statement,
            category=body.category,
            temporal_tag=body.temporal_tag,
            expires_at=body.expires_at,
        )
        await session.commit()

    msg = f"Memory {'saved' if created else 'updated'} successfully (`{record.key}`)"
    LOGGER.info(
        "personal_context_memory_saved",
        native_user_id=body.native_user_id,
        memory_id=str(record.id),
        key=record.key,
        category=record.category,
        created=created,
    )
    return MemorySaveResponse(
        memory=MemoryItemResponse(
            id=record.id,
            key=record.key,
            category=record.category,
            statement=record.statement,
            temporal_tag=record.temporal_tag,
            expires_at=record.expires_at,
            created_at=record.created_at or datetime.now(UTC),
            updated_at=record.updated_at or datetime.now(UTC),
        ),
        created=created,
        message=msg,
    )


@router.post(
    "/memory/update",
    dependencies=[Depends(require_adapter_signature)],
    response_model=MemoryUpdateResponse,
)
async def update_memory(
    body: MemoryUpdateRequest, request: Request
) -> MemoryUpdateResponse:
    """Modify an active memory record by ID."""
    async with request.app.state.session_factory() as session:
        record = await update_direct_memory(
            session,
            native_user_id=body.native_user_id,
            memory_id=body.memory_id,
            statement=body.statement,
            category=body.category,
            temporal_tag=body.temporal_tag,
            expires_at=body.expires_at,
            clear_expiration=body.clear_expiration,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Active memory record not found")
        await session.commit()

    LOGGER.info(
        "personal_context_memory_updated",
        native_user_id=body.native_user_id,
        memory_id=str(record.id),
        key=record.key,
        category=record.category,
    )
    return MemoryUpdateResponse(
        memory=MemoryItemResponse(
            id=record.id,
            key=record.key,
            category=record.category,
            statement=record.statement,
            temporal_tag=record.temporal_tag,
            expires_at=record.expires_at,
            created_at=record.created_at or datetime.now(UTC),
            updated_at=record.updated_at or datetime.now(UTC),
        ),
        message=f"Memory `{record.id}` updated successfully",
    )


@router.post(
    "/memory/forget",
    dependencies=[Depends(require_adapter_signature)],
    response_model=MemoryForgetResponse,
)
async def forget_memory(
    body: MemoryForgetRequest, request: Request
) -> MemoryForgetResponse:
    """Archive one or more active memories by ID or key."""
    async with request.app.state.session_factory() as session:
        records = await forget_direct_memory(
            session,
            native_user_id=body.native_user_id,
            memory_id=body.memory_id,
            key=body.key,
            reason=body.reason,
        )
        if not records:
            raise HTTPException(status_code=404, detail="No active matching memory records found to archive")
        await session.commit()

    archived_ids = [r.id for r in records]
    LOGGER.info(
        "personal_context_memory_forgotten",
        native_user_id=body.native_user_id,
        archived_count=len(archived_ids),
        reason=body.reason,
    )
    return MemoryForgetResponse(
        archived_count=len(archived_ids),
        archived_ids=archived_ids,
        message=f"Archived {len(archived_ids)} memory record(s)",
    )
