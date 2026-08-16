"""Per-user canonical file passages and independent native file references."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assistant_core.db.base import Base


class FileSegment(Base):
    """One exact markdown passage deduplicated per user."""

    __tablename__ = "file_segment"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "content_sha256",
            "chunking_version",
            name="uq_file_segment_user_hash_version",
        ),
        CheckConstraint(
            "char_length(content) <= 4000",
            name="file_segment_content_length",
        ),
        Index(
            "ix_file_segment_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_file_segment_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id"),
        nullable=False,
        index=True,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', content)", persisted=True),
        nullable=False,
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FileReference(Base):
    """Link from a native Open WebUI file to a shared file segment."""

    __tablename__ = "file_reference"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "native_file_id",
            "chunk_ordinal",
            name="uq_file_reference_user_file_chunk",
        ),
        Index(
            "ix_file_reference_user_file_active",
            "user_id",
            "native_file_id",
            postgresql_where=text("tombstoned_at IS NULL"),
        ),
        Index(
            "ix_file_reference_segment_active",
            "segment_id",
            postgresql_where=text("tombstoned_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id"),
        nullable=False,
        index=True,
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.file_segment.id"),
        nullable=False,
        index=True,
    )
    native_file_id: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    header_path: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    chunk_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    tombstoned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
