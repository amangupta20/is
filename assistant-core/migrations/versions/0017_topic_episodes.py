"""Create topic_episode table for hierarchical conversation continuity.

Revision ID: 0017_topic_episodes
Revises: 0016_memory_audit_logs
Create Date: 2026-08-22 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0017_topic_episodes"
down_revision: str | None = "0016_memory_audit_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create topic_episode table with HNSW vector, GIN FTS, and metadata indices."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector SCHEMA extensions"))

    op.create_table(
        "topic_episode",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_core.user_identity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("native_chat_id", sa.String(length=200), nullable=False),
        sa.Column("native_project_id", sa.String(length=200), nullable=True),
        sa.Column("native_folder_id", sa.String(length=200), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("topic_category", sa.String(length=64), nullable=False, server_default="general"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("decisions_made", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("open_loops", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("key_entities", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("start_message_id", sa.String(length=200), nullable=False),
        sa.Column("end_message_id", sa.String(length=200), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(topic_category, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(decisions_made::text, '') || ' ' || coalesce(key_entities::text, ''))",
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
        sa.PrimaryKeyConstraint("id", name="pk_topic_episode"),
        schema="assistant_core",
    )

    op.create_index(
        "ix_topic_episode_user_id",
        "topic_episode",
        ["user_id"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_topic_episode_native_chat_id",
        "topic_episode",
        ["native_chat_id"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_topic_episode_native_project_id",
        "topic_episode",
        ["native_project_id"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_topic_episode_native_folder_id",
        "topic_episode",
        ["native_folder_id"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_topic_episode_user_chat_created",
        "topic_episode",
        ["user_id", "native_chat_id", "created_at"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "ix_topic_episode_search_vector",
        "topic_episode",
        ["search_vector"],
        unique=False,
        schema="assistant_core",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_topic_episode_embedding_hnsw",
        "topic_episode",
        ["embedding"],
        unique=False,
        schema="assistant_core",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Drop topic_episode table and indices."""
    op.drop_table("topic_episode", schema="assistant_core")
