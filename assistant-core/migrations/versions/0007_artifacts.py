"""Create artifacts, artifact_versions, and onlyoffice_sessions tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_artifacts"
down_revision: str | None = "0006_file_awareness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create artifacts and OnlyOffice session tables."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.create_table(
        "artifacts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("current_version_num", sa.Integer(), server_default="1", nullable=False),
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
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_artifacts_user_id_user_identity"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
        schema="assistant_core",
    )
    op.create_index(
        "ix_artifacts_user_tombstone",
        "artifacts",
        ["user_id", "tombstoned_at"],
        unique=False,
        schema="assistant_core",
    )

    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("artifact_id", sa.UUID(), nullable=False),
        sa.Column("version_num", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("change_summary", sa.Text(), server_default="", nullable=False),
        sa.Column("created_by_turn_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["assistant_core.artifacts.id"],
            name=op.f("fk_artifact_versions_artifact_id_artifacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_turn_id"],
            ["assistant_core.completed_turn.id"],
            name=op.f("fk_artifact_versions_created_by_turn_id_completed_turn"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifact_versions")),
        sa.UniqueConstraint(
            "artifact_id",
            "version_num",
            name="uq_artifact_version_num",
        ),
        schema="assistant_core",
    )
    op.create_index(
        "ix_artifact_versions_artifact_id",
        "artifact_versions",
        ["artifact_id"],
        unique=False,
        schema="assistant_core",
    )

    op.create_table(
        "onlyoffice_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("artifact_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("session_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["assistant_core.artifacts.id"],
            name=op.f("fk_onlyoffice_sessions_artifact_id_artifacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_onlyoffice_sessions_user_id_user_identity"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onlyoffice_sessions")),
        schema="assistant_core",
    )
    op.create_index(
        "ix_onlyoffice_sessions_session_key",
        "onlyoffice_sessions",
        ["session_key"],
        unique=False,
        schema="assistant_core",
    )


def downgrade() -> None:
    """Drop artifact and OnlyOffice session tables."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.drop_index("ix_onlyoffice_sessions_session_key", table_name="onlyoffice_sessions", schema="assistant_core")
    op.drop_table("onlyoffice_sessions", schema="assistant_core")
    op.drop_index("ix_artifact_versions_artifact_id", table_name="artifact_versions", schema="assistant_core")
    op.drop_table("artifact_versions", schema="assistant_core")
    op.drop_index("ix_artifacts_user_tombstone", table_name="artifacts", schema="assistant_core")
    op.drop_table("artifacts", schema="assistant_core")
