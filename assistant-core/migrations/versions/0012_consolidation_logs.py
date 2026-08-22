"""Create consolidation_run table for memory consolidation change tracking.

Revision ID: 0012_consolidation_logs
Revises: 0011_temporal_memory
Create Date: 2026-08-17 17:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_consolidation_logs"
down_revision: str | None = "0011_temporal_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create assistant_core.consolidation_run table and indexes."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS assistant_core.consolidation_run (
                id UUID PRIMARY KEY,
                user_id UUID NULL REFERENCES assistant_core.user_identity(id) ON DELETE SET NULL,
                native_user_id VARCHAR(200) NOT NULL,
                trigger VARCHAR(50) NOT NULL,
                status VARCHAR(50) NOT NULL,
                memories_scanned INTEGER NOT NULL DEFAULT 0,
                superseded_count INTEGER NOT NULL DEFAULT 0,
                details JSONB NOT NULL DEFAULT '[]'::jsonb,
                error_message TEXT NULL,
                duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            );
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_consolidation_run_user_time
            ON assistant_core.consolidation_run (native_user_id, created_at DESC);
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_consolidation_run_created_at
            ON assistant_core.consolidation_run (created_at DESC);
            """
        )
    )


def downgrade() -> None:
    """Drop assistant_core.consolidation_run table."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.execute(sa.text("DROP TABLE IF EXISTS assistant_core.consolidation_run CASCADE;"))
