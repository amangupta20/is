"""Signed hybrid personal-memory and conversation-context routes."""

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
    source_type: Literal["memory", "conversation"]
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
    """Search active explicit memory and owner-scoped conversation evidence."""
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
