"""Inferred memory kind support and transient pasted-file classification.

Revision ID: 0019_inferred_transient
Revises: 0018_media_understanding
Create Date: 2026-08-23 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_inferred_transient"
down_revision: str | None = "0018_media_understanding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow inferred memory records and mark pasted-text files as transient."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.drop_constraint(
        "memory_record_kind", "memory_record", schema="assistant_core", type_="check"
    )
    op.create_check_constraint(
        "memory_record_kind",
        "memory_record",
        "kind IN ('explicit', 'inferred')",
        schema="assistant_core",
    )
    op.drop_constraint(
        "memory_record_confidence",
        "memory_record",
        schema="assistant_core",
        type_="check",
    )
    op.create_check_constraint(
        "memory_record_confidence",
        "memory_record",
        "confidence IN (0, 1)",
        schema="assistant_core",
    )

    op.add_column(
        "file_document",
        sa.Column(
            "transient",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="assistant_core",
    )
    op.add_column(
        "file_reference",
        sa.Column(
            "transient",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="assistant_core",
    )


def downgrade() -> None:
    """Restore explicit-only memory constraints and drop transient columns."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    op.drop_column("file_reference", "transient", schema="assistant_core")
    op.drop_column("file_document", "transient", schema="assistant_core")

    op.execute(
        sa.text(
            "DELETE FROM assistant_core.memory_record "
            "WHERE kind <> 'explicit' OR confidence NOT IN (1)"
        )
    )
    op.drop_constraint(
        "memory_record_confidence",
        "memory_record",
        schema="assistant_core",
        type_="check",
    )
    op.create_check_constraint(
        "memory_record_confidence",
        "memory_record",
        "confidence = 1",
        schema="assistant_core",
    )
    op.execute(sa.text("DELETE FROM assistant_core.memory_record WHERE kind <> 'explicit'"))
    op.drop_constraint(
        "memory_record_kind", "memory_record", schema="assistant_core", type_="check"
    )
    op.create_check_constraint(
        "memory_record_kind",
        "memory_record",
        "kind = 'explicit'",
        schema="assistant_core",
    )
