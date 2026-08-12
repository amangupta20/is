"""Create per-user canonical file passages and bounded references."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0006_file_awareness"
down_revision: str | None = "0005_conversation_recall"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create file segments and references with the same hybrid indexes."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector SCHEMA extensions"))
    op.create_table(
        "file_segment",
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
            name=op.f("ck_file_segment_content_length"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_file_segment_user_id_user_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_segment")),
        sa.UniqueConstraint(
            "user_id",
            "content_sha256",
            "chunking_version",
            name="uq_file_segment_user_hash_version",
        ),
        schema="assistant_core",
    )
    op.create_index(
        "ix_file_segment_search_vector",
        "file_segment",
        ["search_vector"],
        unique=False,
        schema="assistant_core",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_file_segment_embedding_hnsw",
        "file_segment",
        ["embedding"],
        unique=False,
        schema="assistant_core",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "file_reference",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("native_file_id", sa.String(length=200), nullable=False),
        sa.Column("chunk_ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(native_file_id) <= 200",
            name=op.f("ck_file_reference_file_id_length"),
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["assistant_core.file_segment.id"],
            name=op.f("fk_file_reference_segment_id_file_segment"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_file_reference_user_id_user_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_reference")),
        sa.UniqueConstraint(
            "user_id",
            "native_file_id",
            "chunk_ordinal",
            name="uq_file_reference_user_file_chunk",
        ),
        schema="assistant_core",
    )
    op.create_index(
        "ix_file_reference_user_file_active",
        "file_reference",
        ["user_id", "native_file_id"],
        unique=False,
        schema="assistant_core",
        postgresql_where=sa.text("tombstoned_at IS NULL"),
    )
    op.create_index(
        "ix_file_reference_segment_active",
        "file_reference",
        ["segment_id"],
        unique=False,
        schema="assistant_core",
        postgresql_where=sa.text("tombstoned_at IS NULL"),
    )


def downgrade() -> None:
    """Drop file awareness objects in reverse dependency order."""
    op.drop_index(
        "ix_file_reference_segment_active",
        table_name="file_reference",
        schema="assistant_core",
    )
    op.drop_index(
        "ix_file_reference_user_file_active",
        table_name="file_reference",
        schema="assistant_core",
    )
    op.drop_table("file_reference", schema="assistant_core")
    op.drop_index(
        "ix_file_segment_embedding_hnsw",
        table_name="file_segment",
        schema="assistant_core",
    )
    op.drop_index(
        "ix_file_segment_search_vector",
        table_name="file_segment",
        schema="assistant_core",
    )
    op.drop_table("file_segment", schema="assistant_core")
