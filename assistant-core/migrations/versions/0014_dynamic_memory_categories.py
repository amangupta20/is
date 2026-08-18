"""Allow dynamic memory categories and drop rigid enum check constraint on memory_record.

Revision ID: 0014_dynamic_memory_categories
Revises: 0013_kb_reconciliation_logs
Create Date: 2026-08-18 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_dynamic_memory_categories"
down_revision: str | None = "0013_kb_reconciliation_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop rigid category check constraint and support dynamic category slugs."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    # Drop the legacy category enum constraint
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS memory_record_category;
            """
        )
    )

    # Ensure category column supports up to 50 characters
    op.alter_column(
        "memory_record",
        "category",
        existing_type=sa.String(30),
        type_=sa.String(50),
        schema="assistant_core",
    )

    # Add length sanity constraint
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            ADD CONSTRAINT memory_record_category_len
            CHECK (char_length(category) >= 2 AND char_length(category) <= 50);
            """
        )
    )


def downgrade() -> None:
    """Restore legacy category check constraint."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS memory_record_category_len;
            """
        )
    )

    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            ADD CONSTRAINT memory_record_category
            CHECK (category IN ('fact', 'preference', 'instruction', 'project', 'decision'));
            """
        )
    )
