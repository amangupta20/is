"""Add temporal validity columns and index to memory_record table.

Revision ID: 0011_temporal_memory
Revises: 0010_project_folder_scoping
Create Date: 2026-08-17 16:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_temporal_memory"
down_revision: str | None = "0010_project_folder_scoping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add valid_from, expires_at, and temporal_tag columns with validity index."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP WITH TIME ZONE NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            ADD COLUMN IF NOT EXISTS temporal_tag VARCHAR(50) NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_memory_record_user_validity
            ON assistant_core.memory_record (user_id, state, expires_at);
            """
        )
    )


def downgrade() -> None:
    """Remove temporal validity columns and index from memory_record."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.execute(sa.text("DROP INDEX IF EXISTS assistant_core.ix_memory_record_user_validity;"))
    op.execute(
        sa.text("ALTER TABLE assistant_core.memory_record DROP COLUMN IF EXISTS temporal_tag;")
    )
    op.execute(
        sa.text("ALTER TABLE assistant_core.memory_record DROP COLUMN IF EXISTS expires_at;")
    )
    op.execute(
        sa.text("ALTER TABLE assistant_core.memory_record DROP COLUMN IF EXISTS valid_from;")
    )
