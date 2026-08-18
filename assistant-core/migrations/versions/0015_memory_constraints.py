"""Drop legacy check constraints on memory_record table.

Revision ID: 0015_memory_constraints
Revises: 0014_dynamic_memory_categories
Create Date: 2026-08-18 16:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_memory_constraints"
down_revision: str | None = "0014_dynamic_memory_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Explicitly drop legacy category and state constraints and recreate them."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    # Drop all variations of category check constraints individually
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_category"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record DROP CONSTRAINT IF EXISTS memory_record_category"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_category_len"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record DROP CONSTRAINT IF EXISTS memory_record_category_len"
        )
    )

    # Drop all variations of state check constraints individually
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_state"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record DROP CONSTRAINT IF EXISTS memory_record_state"
        )
    )

    # Ensure category column length is 50
    op.alter_column(
        "memory_record",
        "category",
        existing_type=sa.String(30),
        type_=sa.String(50),
        schema="assistant_core",
    )

    # Add updated clean constraints individually
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record ADD CONSTRAINT ck_memory_record_memory_record_category_len CHECK (char_length(category) >= 2 AND char_length(category) <= 50)"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record ADD CONSTRAINT ck_memory_record_memory_record_state CHECK (state IN ('active', 'superseded', 'archived', 'expired'))"
        )
    )


def downgrade() -> None:
    """Restore legacy constraints."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_category_len"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record DROP CONSTRAINT IF EXISTS ck_memory_record_memory_record_state"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record ADD CONSTRAINT ck_memory_record_memory_record_category CHECK (category IN ('fact', 'preference', 'instruction', 'project', 'decision'))"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE assistant_core.memory_record ADD CONSTRAINT ck_memory_record_memory_record_state CHECK (state IN ('active', 'superseded', 'archived'))"
        )
    )
