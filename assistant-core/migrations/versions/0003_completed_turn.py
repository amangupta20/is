"""Create durable completed-turn records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_completed_turn"
down_revision: str | None = "0002_event_inbox_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the completed-turn table and its native-chat lookup index."""
    op.create_table(
        "completed_turn",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("native_chat_id", sa.String(length=200), nullable=False),
        sa.Column("native_user_message_id", sa.String(length=200), nullable=False),
        sa.Column(
            "native_assistant_message_id", sa.String(length=200), nullable=False
        ),
        sa.Column("user_content", sa.Text(), nullable=False),
        sa.Column("assistant_content", sa.Text(), nullable=False),
        sa.Column("user_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("assistant_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_completed_turn_user_id_user_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_completed_turn")),
        sa.UniqueConstraint("event_id", name=op.f("uq_completed_turn_event_id")),
        schema="assistant_core",
    )
    op.create_index(
        op.f("ix_assistant_core_completed_turn_native_chat_id"),
        "completed_turn",
        ["native_chat_id"],
        unique=False,
        schema="assistant_core",
    )


def downgrade() -> None:
    """Drop completed-turn objects in reverse dependency order."""
    op.drop_index(
        op.f("ix_assistant_core_completed_turn_native_chat_id"),
        table_name="completed_turn",
        schema="assistant_core",
    )
    op.drop_table("completed_turn", schema="assistant_core")
