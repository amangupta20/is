"""Signed, inspectable personal-memory search and read routes."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.memory.repository import read_explicit_memory, search_explicit_memory

router = APIRouter(prefix="/v1/personal-context", tags=["personal-context"])


class PersonalContextSearchRequest(BaseModel):
    """Bounded native identity and lexical search request."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    native_chat_id: str | None = Field(default=None, min_length=1, max_length=200)
    native_message_id: str | None = Field(default=None, min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=1_000)
    limit: int = Field(default=5, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        """Require a nonblank query after stripping surrounding whitespace."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class PersonalContextPreview(BaseModel):
    """One opaque source ID and compact lexical preview."""

    model_config = ConfigDict(extra="forbid")

    memory_source_id: uuid.UUID
    category: str
    preview: str


class PersonalContextSearchResponse(BaseModel):
    """Stable lexical search envelope shared by future source indexes."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["lexical"]
    results: list[PersonalContextPreview]


class PersonalContextReadRequest(BaseModel):
    """Native identity and one opaque memory source ID."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    memory_source_id: uuid.UUID


class PersonalContextReadResponse(BaseModel):
    """Source-linked evidence with expansion boundaries made explicit."""

    model_config = ConfigDict(extra="forbid")

    memory_source_id: uuid.UUID
    statement: str
    category: str
    evidence_quote: str
    source_native_chat_id: str
    source_native_message_id: str
    neighboring_available: bool
    full_source_available: bool


@router.post(
    "/search",
    dependencies=[Depends(require_adapter_signature)],
    response_model=PersonalContextSearchResponse,
)
async def search_personal_context(
    body: PersonalContextSearchRequest, request: Request
) -> PersonalContextSearchResponse:
    """Search active explicit memory using the signed native identity."""
    async with request.app.state.session_factory() as session:
        records = await search_explicit_memory(
            session,
            native_user_id=body.native_user_id,
            query=body.query,
            limit=body.limit,
        )
    return PersonalContextSearchResponse(
        mode="lexical",
        results=[
            PersonalContextPreview(
                memory_source_id=record.id,
                category=record.category,
                preview=" ".join(record.statement.split()),
            )
            for record in records
        ],
    )


@router.post(
    "/read",
    dependencies=[Depends(require_adapter_signature)],
    response_model=PersonalContextReadResponse,
)
async def read_personal_context(
    body: PersonalContextReadRequest, request: Request
) -> PersonalContextReadResponse:
    """Read one source-linked memory only for its owning native user."""
    async with request.app.state.session_factory() as session:
        result = await read_explicit_memory(
            session,
            native_user_id=body.native_user_id,
            memory_source_id=body.memory_source_id,
        )
    if result is None:
        raise HTTPException(status_code=404, detail="memory source not found")
    record, evidence, turn = result
    return PersonalContextReadResponse(
        memory_source_id=record.id,
        statement=record.statement,
        category=record.category,
        evidence_quote=evidence.evidence_quote,
        source_native_chat_id=turn.native_chat_id,
        source_native_message_id=turn.native_user_message_id,
        neighboring_available=False,
        full_source_available=False,
    )
