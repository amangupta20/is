"""Topic episode model for high-density multi-turn session summaries."""

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


class TopicEpisode(Base):
    """A high-density semantic topic episode extracted from an inactive or completed conversation."""

    __tablename__ = "topic_episode"
    __table_args__ = (
        Index(
            "ix_topic_episode_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_topic_episode_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_topic_episode_user_chat_created",
            "user_id",
            "native_chat_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id"),
        nullable=False,
        index=True,
    )
    native_chat_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    native_project_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    native_folder_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    topic_category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    decisions_made: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    open_loops: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    key_entities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    start_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    end_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            text(
                "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(topic_category, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(decisions_made::text, '') || ' ' || coalesce(key_entities::text, ''))"
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
