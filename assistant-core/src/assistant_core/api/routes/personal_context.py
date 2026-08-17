"""Signed hybrid personal-memory, conversation-context, and file-passage routes."""

import asyncio
import uuid
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
from assistant_core.files.repository import (
    get_full_file_content,
    read_file_passage_context,
    search_file_passages,
)
from assistant_core.memory.repository import read_explicit_memory, search_explicit_memory

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
    source_type: Literal["memory", "conversation", "file"]
    category: str
    role: Literal["user", "assistant"] | None
    preview: str
    source_native_chat_id: str | None
    source_native_message_id: str | None
    source_native_project_id: str | None = None
    source_native_folder_id: str | None = None
    filename: str | None = None
    native_file_id: str | None = None
    header_path: str | None = None
    chunk_ordinal: int | None = None


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
    source_type: Literal["memory", "conversation", "file"]
    content: str
    category: str
    role: Literal["user", "assistant"] | None
    evidence_quote: str | None
    source_native_chat_id: str | None
    source_native_message_id: str | None
    source_native_project_id: str | None = None
    source_native_folder_id: str | None = None
    filename: str | None = None
    native_file_id: str | None = None
    header_path: str | None = None
    chunk_ordinal: int | None = None
    previous_chunk: str | None = None
    next_chunk: str | None = None
    neighbors: list[PersonalContextNeighbor] = Field(default_factory=list)
    full_source_available: bool = False


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
                neighbors=[],
                full_source_available=False,
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
