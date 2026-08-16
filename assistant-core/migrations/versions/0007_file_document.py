"""Create per-user full file document table for un-chunked document retrieval."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_file_document"
down_revision: str | None = "0006_file_awareness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create file_document table for full document persistence."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.create_table(
        "file_document",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("native_file_id", sa.String(length=200), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("total_characters", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_file_document_user_id_user_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_document")),
        sa.UniqueConstraint(
            "user_id",
            "native_file_id",
            name="uq_file_document_user_file",
        ),
        schema="assistant_core",
    )
    op.create_index(
        "ix_file_document_user_file_active",
        "file_document",
        ["user_id", "native_file_id"],
        unique=False,
        schema="assistant_core",
        postgresql_where=sa.text("tombstoned_at IS NULL"),
    )


def downgrade() -> None:
    """Drop file_document table."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.drop_index(
        "ix_file_document_user_file_active",
        table_name="file_document",
        schema="assistant_core",
    )
    op.drop_table("file_document", schema="assistant_core")
