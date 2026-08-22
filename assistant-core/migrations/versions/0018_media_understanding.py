"""Create media_document and media_segment tables for multimodal media understanding.

Revision ID: 0018_media_understanding
Revises: 0017_topic_episodes
Create Date: 2026-08-22 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0018_media_understanding"
down_revision: str | None = "0017_topic_episodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create media_document and media_segment tables with HNSW vector and GIN FTS indices."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector SCHEMA extensions"))

    op.create_table(
        "media_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_core.user_identity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False, server_default="youtube"),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("channel_or_author", sa.String(length=256), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("key_takeaways", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("topics", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("total_segments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(channel_or_author, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(key_takeaways::text, '') || ' ' || coalesce(topics::text, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_document"),
        schema="assistant_core",
    )

    op.create_index(
        "ix_media_document_user_id",
        "media_document",
        ["user_id"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_media_document_url",
        "media_document",
        ["url"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_media_document_user_url",
        "media_document",
        ["user_id", "url"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_media_document_search_vector",
        "media_document",
        ["search_vector"],
        unique=False,
        schema="assistant_core",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_media_document_embedding_hnsw",
        "media_document",
        ["embedding"],
        unique=False,
        schema="assistant_core",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "media_segment",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_core.media_document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_core.user_identity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("segment_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_time_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end_time_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(label, '') || ' ' || coalesce(content, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_segment"),
        schema="assistant_core",
    )

    op.create_index(
        "ix_media_segment_document_id",
        "media_segment",
        ["document_id"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_media_segment_user_id",
        "media_segment",
        ["user_id"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_media_segment_doc_index",
        "media_segment",
        ["document_id", "segment_index"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_media_segment_search_vector",
        "media_segment",
        ["search_vector"],
        unique=False,
        schema="assistant_core",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_media_segment_embedding_hnsw",
        "media_segment",
        ["embedding"],
        unique=False,
        schema="assistant_core",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Drop media_segment and media_document tables."""
    op.drop_table("media_segment", schema="assistant_core")
    op.drop_table("media_document", schema="assistant_core")
