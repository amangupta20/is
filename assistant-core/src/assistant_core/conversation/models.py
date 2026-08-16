"""Per-user canonical passages and independent native message references."""

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


class ConversationSegment(Base):
    """One exact passage reused only inside a single user's source graph."""

    __tablename__ = "conversation_segment"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "content_sha256",
            "chunking_version",
            name="uq_conversation_segment_user_hash_version",
        ),
        CheckConstraint(
            "char_length(content) <= 4000",
            name="conversation_segment_content_length",
        ),
        Index(
            "ix_conversation_segment_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_conversation_segment_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id"),
        nullable=False,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', content)", persisted=True),
        nullable=False,
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    embedding_version: Mapped[str | None] = mapped_column(String(80))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversationReference(Base):
    """One owner-scoped native message/chunk reference to canonical text."""

    __tablename__ = "conversation_reference"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "native_chat_id",
            "native_message_id",
            "role",
            "chunk_ordinal",
            name="uq_conversation_reference_native_chunk",
        ),
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="conversation_reference_role",
        ),
        Index(
            "ix_conversation_reference_user_chat_active",
            "user_id",
            "native_chat_id",
            postgresql_where=text("tombstoned_at IS NULL"),
        ),
        Index(
            "ix_conversation_reference_segment_active",
            "segment_id",
            postgresql_where=text("tombstoned_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id"),
        nullable=False,
    )
    completed_turn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.completed_turn.id"),
        nullable=False,
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.conversation_segment.id"),
        nullable=False,
    )
    native_chat_id: Mapped[str] = mapped_column(String(200), nullable=False)
    native_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    role_order: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    tombstoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
