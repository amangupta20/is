"""Create memory_change_log table for granular audit tracking and rollbacks.

Revision ID: 0016_memory_audit_logs
Revises: 0015_memory_constraints
Create Date: 2026-08-20 10:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_memory_audit_logs"
down_revision: str | None = "0015_memory_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create memory_change_log table with audit fields and indices."""
    op.create_table(
        "memory_change_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_core.user_identity.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("native_user_id", sa.String(200), nullable=False),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_core.memory_record.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("change_source", sa.String(50), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("previous_state", postgresql.JSONB, nullable=True),
        sa.Column("new_state", postgresql.JSONB, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("is_reverted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="assistant_core",
    )

    op.create_index(
        "ix_memory_change_log_user_time",
        "memory_change_log",
        ["native_user_id", "created_at"],
        schema="assistant_core",
        postgresql_using="btree",
    )
    op.create_index(
        "ix_memory_change_log_memory_id",
        "memory_change_log",
        ["memory_id"],
        schema="assistant_core",
        postgresql_using="btree",
    )
    op.create_index(
        "ix_memory_change_log_created_at",
        "memory_change_log",
        ["created_at"],
        schema="assistant_core",
        postgresql_using="btree",
    )


def downgrade() -> None:
    """Drop memory_change_log table."""
    op.drop_index(
        "ix_memory_change_log_created_at",
        table_name="memory_change_log",
        schema="assistant_core",
    )
    op.drop_index(
        "ix_memory_change_log_memory_id",
        table_name="memory_change_log",
        schema="assistant_core",
    )
    op.drop_index(
        "ix_memory_change_log_user_time",
        table_name="memory_change_log",
        schema="assistant_core",
    )
    op.drop_table("memory_change_log", schema="assistant_core")
