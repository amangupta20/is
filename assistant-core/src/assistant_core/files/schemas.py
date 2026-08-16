"""Schemas for file awareness, chunking, and search."""

from pydantic import BaseModel, ConfigDict, Field


class MarkdownChunk(BaseModel):
    """Represents a bounded, header-aware markdown passage."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=4000)
    header_path: str = Field(default="")
    chunk_ordinal: int = Field(ge=0)
    content_sha256: str = Field(min_length=64, max_length=64)


class FileMaterializationResult(BaseModel):
    """Summary of segment and reference materialization."""

    model_config = ConfigDict(frozen=True)

    native_file_id: str
    total_chunks: int
    inserted_segments: int
    reused_segments: int
    missing_embedding_segment_ids: tuple[str, ...]


class FileHit(BaseModel):
    """Hybrid search hit for a file passage."""

    model_config = ConfigDict(frozen=True)

    reference_id: str
    segment_id: str
    native_file_id: str
    filename: str
    header_path: str
    content: str
    chunk_ordinal: int
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    score: float = 0.0


class FilePassageContext(BaseModel):
    """Bounded file passage context with neighbors."""

    model_config = ConfigDict(frozen=True)

    reference_id: str
    native_file_id: str
    filename: str
    mime_type: str
    header_path: str
    chunk_ordinal: int
    content: str
    previous_content: str | None = None
    next_content: str | None = None


class FullFileContent(BaseModel):
    """Complete reconstructed document content from ordered segments."""

    model_config = ConfigDict(frozen=True)

    native_file_id: str
    filename: str
    mime_type: str
    total_chunks: int
    total_characters: int
    content: str

