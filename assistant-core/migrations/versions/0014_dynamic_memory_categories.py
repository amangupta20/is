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
    """Drop rigid category check constraint, support dynamic category slugs, and allow expired state."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    # Drop all possible category check constraints
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS memory_record_category;
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_category;
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS memory_record_category_len;
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_category_len;
            """
        )
    )

    # Drop all possible state check constraints
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS memory_record_state;
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_state;
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

    # Add updated constraints
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            ADD CONSTRAINT ck_memory_record_memory_record_category_len
            CHECK (char_length(category) >= 2 AND char_length(category) <= 50);

            ALTER TABLE assistant_core.memory_record
            ADD CONSTRAINT ck_memory_record_memory_record_state
            CHECK (state IN ('active', 'superseded', 'archived', 'expired'));
            """
        )
    )


def downgrade() -> None:
    """Restore legacy category and state check constraints."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_category_len;
            ALTER TABLE assistant_core.memory_record
            DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_state;
            ALTER TABLE assistant_core.memory_record
            ADD CONSTRAINT ck_memory_record_memory_record_category
            CHECK (category IN ('fact', 'preference', 'instruction', 'project', 'decision'));
            ALTER TABLE assistant_core.memory_record
            ADD CONSTRAINT ck_memory_record_memory_record_state
            CHECK (state IN ('active', 'superseded', 'archived'));
            """
        )
    )
