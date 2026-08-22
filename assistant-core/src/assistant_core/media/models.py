"""SQLAlchemy models for multimodal media understanding."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assistant_core.db.base import Base


class MediaDocument(Base):
    """High-level media document storing overall summary and metadata."""

    __tablename__ = "media_document"
    __table_args__ = (
        Index(
            "ix_media_document_user_id",
            "user_id",
        ),
        Index(
            "ix_media_document_url",
            "url",
        ),
        Index(
            "ix_media_document_user_url",
            "user_id",
            "url",
        ),
        Index(
            "ix_media_document_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_media_document_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False, default="youtube")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_or_author: Mapped[str | None] = mapped_column(String(256), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    key_takeaways: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    topics: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    total_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            text(
                "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(channel_or_author, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(key_takeaways::text, '') || ' ' || coalesce(topics::text, ''))"
            ),
            persisted=True,
        ),
        nullable=False,
    )

    tombstoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MediaSegment(Base):
    """Timestamped chunk or chapter of a media document."""

    __tablename__ = "media_segment"
    __table_args__ = (
        Index(
            "ix_media_segment_document_id",
            "document_id",
        ),
        Index(
            "ix_media_segment_user_id",
            "user_id",
        ),
        Index(
            "ix_media_segment_doc_index",
            "document_id",
            "segment_index",
        ),
        Index(
            "ix_media_segment_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_media_segment_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.media_document.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_time_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_time_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            text(
                "to_tsvector('english', coalesce(label, '') || ' ' || coalesce(content, ''))"
            ),
            persisted=True,
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
