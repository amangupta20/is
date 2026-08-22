"""Create the source-linked explicit-memory ledger."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_explicit_memory"
down_revision: str | None = "0003_completed_turn"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create retained memory records, evidence, and chat profile snapshots."""
    op.create_table(
        "memory_record",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "kind", sa.String(length=30), server_default=sa.text("'explicit'"), nullable=False
        ),
        sa.Column("confidence", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "state", sa.String(length=30), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "category IN ('fact', 'preference', 'instruction', 'project', 'decision')",
            name=op.f("ck_memory_record_memory_record_category"),
        ),
        sa.CheckConstraint("kind = 'explicit'", name=op.f("ck_memory_record_memory_record_kind")),
        sa.CheckConstraint(
            "confidence = 1", name=op.f("ck_memory_record_memory_record_confidence")
        ),
        sa.CheckConstraint(
            "char_length(statement) <= 2000",
            name=op.f("ck_memory_record_memory_record_statement_length"),
        ),
        sa.CheckConstraint(
            "state IN ('active', 'superseded', 'archived')",
            name=op.f("ck_memory_record_memory_record_state"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["assistant_core.memory_record.id"],
            name=op.f("fk_memory_record_superseded_by_id_memory_record"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_memory_record_user_id_user_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_record")),
        schema="assistant_core",
    )
    op.create_index(
        "ix_memory_record_user_id_key",
        "memory_record",
        ["user_id", "key"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        "uq_memory_record_active_user_key",
        "memory_record",
        ["user_id", "key"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
        schema="assistant_core",
    )
    op.create_table(
        "memory_evidence",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("memory_record_id", sa.UUID(), nullable=False),
        sa.Column("completed_turn_id", sa.UUID(), nullable=False),
        sa.Column("native_user_message_id", sa.String(length=200), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(evidence_quote) <= 1000",
            name=op.f("ck_memory_evidence_memory_evidence_evidence_quote_length"),
        ),
        sa.ForeignKeyConstraint(
            ["completed_turn_id"],
            ["assistant_core.completed_turn.id"],
            name=op.f("fk_memory_evidence_completed_turn_id_completed_turn"),
        ),
        sa.ForeignKeyConstraint(
            ["memory_record_id"],
            ["assistant_core.memory_record.id"],
            name=op.f("fk_memory_evidence_memory_record_id_memory_record"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_evidence")),
        schema="assistant_core",
    )
    op.create_index(
        "uq_memory_evidence_record_turn_quote",
        "memory_evidence",
        ["memory_record_id", "completed_turn_id", "evidence_quote"],
        unique=True,
        schema="assistant_core",
    )
    op.create_table(
        "chat_profile_snapshot",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("native_chat_id", sa.String(length=200), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("source_memory_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_chat_profile_snapshot_user_id_user_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_profile_snapshot")),
        schema="assistant_core",
    )
    op.create_index(
        "uq_chat_profile_snapshot_user_chat",
        "chat_profile_snapshot",
        ["user_id", "native_chat_id"],
        unique=True,
        schema="assistant_core",
    )


def downgrade() -> None:
    """Drop explicit-memory ledger objects in reverse dependency order."""
    op.drop_index(
        "uq_chat_profile_snapshot_user_chat",
        table_name="chat_profile_snapshot",
        schema="assistant_core",
    )
    op.drop_table("chat_profile_snapshot", schema="assistant_core")
    op.drop_index(
        "uq_memory_evidence_record_turn_quote",
        table_name="memory_evidence",
        schema="assistant_core",
    )
    op.drop_table("memory_evidence", schema="assistant_core")
    op.drop_index(
        "uq_memory_record_active_user_key", table_name="memory_record", schema="assistant_core"
    )
    op.drop_index(
        "ix_memory_record_user_id_key", table_name="memory_record", schema="assistant_core"
    )
    op.drop_table("memory_record", schema="assistant_core")
