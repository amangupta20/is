"""Create bounded conversation segments and source references."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0005_conversation_recall"
down_revision: str | None = "0004_explicit_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create per-user canonical passages and independent native references."""
    op.create_table(
        "conversation_segment",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("chunking_version", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', content)", persisted=True),
            nullable=False,
        ),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("embedding_version", sa.String(length=80), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(content) <= 4000",
            name=op.f("ck_conversation_segment_conversation_segment_content_length"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_conversation_segment_user_id_user_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_segment")),
        sa.UniqueConstraint(
            "user_id",
            "content_sha256",
            "chunking_version",
            name="uq_conversation_segment_user_hash_version",
        ),
        schema="assistant_core",
    )
    op.create_index(
        "ix_conversation_segment_search_vector",
        "conversation_segment",
        ["search_vector"],
        unique=False,
        schema="assistant_core",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_conversation_segment_embedding_hnsw",
        "conversation_segment",
        ["embedding"],
        unique=False,
        schema="assistant_core",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "conversation_reference",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("completed_turn_id", sa.UUID(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("native_chat_id", sa.String(length=200), nullable=False),
        sa.Column("native_message_id", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("role_order", sa.Integer(), nullable=False),
        sa.Column("chunk_ordinal", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name=op.f("ck_conversation_reference_conversation_reference_role"),
        ),
        sa.ForeignKeyConstraint(
            ["completed_turn_id"],
            ["assistant_core.completed_turn.id"],
            name=op.f("fk_conversation_reference_completed_turn_id_completed_turn"),
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["assistant_core.conversation_segment.id"],
            name=op.f("fk_conversation_reference_segment_id_conversation_segment"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_conversation_reference_user_id_user_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_reference")),
        sa.UniqueConstraint(
            "user_id",
            "native_chat_id",
            "native_message_id",
            "role",
            "chunk_ordinal",
            name="uq_conversation_reference_native_chunk",
        ),
        schema="assistant_core",
    )
    op.create_index(
        "ix_conversation_reference_user_chat_active",
        "conversation_reference",
        ["user_id", "native_chat_id"],
        unique=False,
        schema="assistant_core",
        postgresql_where=sa.text("tombstoned_at IS NULL"),
    )
    op.create_index(
        "ix_conversation_reference_segment_active",
        "conversation_reference",
        ["segment_id"],
        unique=False,
        schema="assistant_core",
        postgresql_where=sa.text("tombstoned_at IS NULL"),
    )


def downgrade() -> None:
    """Drop conversation recall objects in reverse dependency order."""
    op.drop_index(
        "ix_conversation_reference_segment_active",
        table_name="conversation_reference",
        schema="assistant_core",
    )
    op.drop_index(
        "ix_conversation_reference_user_chat_active",
        table_name="conversation_reference",
        schema="assistant_core",
    )
    op.drop_table("conversation_reference", schema="assistant_core")
    op.drop_index(
        "ix_conversation_segment_embedding_hnsw",
        table_name="conversation_segment",
        schema="assistant_core",
    )
    op.drop_index(
        "ix_conversation_segment_search_vector",
        table_name="conversation_segment",
        schema="assistant_core",
    )
    op.drop_table("conversation_segment", schema="assistant_core")
