"""Create durable identity, event inbox, and queued job records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_event_inbox_jobs"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the Slice 1D2A identity, inbox, and job objects."""
    op.create_table(
        "job",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("identity_key", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job")),
        sa.UniqueConstraint("identity_key", name=op.f("uq_job_identity_key")),
        schema="assistant_core",
    )
    op.create_index(
        op.f("ix_assistant_core_job_kind"),
        "job",
        ["kind"],
        unique=False,
        schema="assistant_core",
    )
    op.create_index(
        op.f("ix_assistant_core_job_status"),
        "job",
        ["status"],
        unique=False,
        schema="assistant_core",
    )
    op.create_table(
        "user_identity",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("native_user_id", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_identity")),
        sa.UniqueConstraint(
            "native_user_id", name=op.f("uq_user_identity_native_user_id")
        ),
        schema="assistant_core",
    )
    op.create_table(
        "event_inbox",
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("native_chat_id", sa.String(length=200), nullable=True),
        sa.Column("native_message_id", sa.String(length=200), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["assistant_core.user_identity.id"],
            name=op.f("fk_event_inbox_user_id_user_identity"),
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_event_inbox")),
        schema="assistant_core",
    )
    op.create_index(
        op.f("ix_assistant_core_event_inbox_event_type"),
        "event_inbox",
        ["event_type"],
        unique=False,
        schema="assistant_core",
    )


def downgrade() -> None:
    """Drop only the Slice 1D2A objects in reverse dependency order."""
    op.drop_index(
        op.f("ix_assistant_core_event_inbox_event_type"),
        table_name="event_inbox",
        schema="assistant_core",
    )
    op.drop_table("event_inbox", schema="assistant_core")
    op.drop_table("user_identity", schema="assistant_core")
    op.drop_index(
        op.f("ix_assistant_core_job_status"),
        table_name="job",
        schema="assistant_core",
    )
    op.drop_index(
        op.f("ix_assistant_core_job_kind"),
        table_name="job",
        schema="assistant_core",
    )
    op.drop_table("job", schema="assistant_core")
