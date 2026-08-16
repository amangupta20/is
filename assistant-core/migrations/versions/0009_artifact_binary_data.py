"""Add binary_data column to artifact_versions table.

Revision ID: 0009_artifact_binary_data
Revises: 0008_artifacts
Create Date: 2026-08-16 14:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_artifact_binary_data"
down_revision: str | None = "0008_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add binary_data column and make storage_path nullable."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.artifact_versions
            ADD COLUMN IF NOT EXISTS binary_data BYTEA NOT NULL DEFAULT ''::bytea;
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.artifact_versions
            ALTER COLUMN storage_path DROP NOT NULL;
            """
        )
    )


def downgrade() -> None:
    """Remove binary_data column from artifact_versions."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.artifact_versions
            DROP COLUMN IF EXISTS binary_data;
            """
        )
    )
