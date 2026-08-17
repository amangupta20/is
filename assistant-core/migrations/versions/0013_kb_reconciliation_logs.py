"""Create kb_reconciliation_run table for Knowledge Base document reconciliation audits.

Revision ID: 0013_kb_reconciliation_logs
Revises: 0012_consolidation_logs
Create Date: 2026-08-17 18:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_kb_reconciliation_logs"
down_revision: str | None = "0012_consolidation_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create assistant_core.kb_reconciliation_run table and indexes."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS assistant_core.kb_reconciliation_run (
                id UUID PRIMARY KEY,
                trigger VARCHAR(50) NOT NULL,
                status VARCHAR(50) NOT NULL,
                kb_files_scanned INTEGER NOT NULL DEFAULT 0,
                pruned_count INTEGER NOT NULL DEFAULT 0,
                details JSONB NOT NULL DEFAULT '[]'::jsonb,
                error_message TEXT NULL,
                duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            );
            """
        )
    )

    op.create_index(
        "ix_kb_reconciliation_run_created_at",
        "kb_reconciliation_run",
        ["created_at"],
        schema="assistant_core",
    )
    op.create_index(
        "ix_kb_reconciliation_run_trigger",
        "kb_reconciliation_run",
        ["trigger"],
        schema="assistant_core",
    )


def downgrade() -> None:
    """Drop assistant_core.kb_reconciliation_run table."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.drop_index(
        "ix_kb_reconciliation_run_trigger",
        table_name="kb_reconciliation_run",
        schema="assistant_core",
    )
    op.drop_index(
        "ix_kb_reconciliation_run_created_at",
        table_name="kb_reconciliation_run",
        schema="assistant_core",
    )
    op.drop_table("kb_reconciliation_run", schema="assistant_core")
