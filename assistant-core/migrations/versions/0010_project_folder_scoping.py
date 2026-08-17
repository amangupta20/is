"""Add native_project_id and native_folder_id to completed_turn, conversation_reference, and artifacts.

Revision ID: 0010_project_folder_scoping
Revises: 0009_artifact_binary_data
Create Date: 2026-08-17 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_project_folder_scoping"
down_revision: str | None = "0009_artifact_binary_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add native_project_id and native_folder_id columns with indexes."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    # 1. event_inbox
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.event_inbox
            ADD COLUMN IF NOT EXISTS native_project_id VARCHAR(200) NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.event_inbox
            ADD COLUMN IF NOT EXISTS native_folder_id VARCHAR(200) NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_event_inbox_project
            ON assistant_core.event_inbox (user_id, native_project_id);
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_event_inbox_folder
            ON assistant_core.event_inbox (user_id, native_folder_id);
            """
        )
    )

    # 2. completed_turn
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.completed_turn
            ADD COLUMN IF NOT EXISTS native_project_id VARCHAR(200) NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.completed_turn
            ADD COLUMN IF NOT EXISTS native_folder_id VARCHAR(200) NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_completed_turn_project
            ON assistant_core.completed_turn (user_id, native_project_id);
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_completed_turn_folder
            ON assistant_core.completed_turn (user_id, native_folder_id);
            """
        )
    )

    # 3. conversation_reference
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.conversation_reference
            ADD COLUMN IF NOT EXISTS native_project_id VARCHAR(200) NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.conversation_reference
            ADD COLUMN IF NOT EXISTS native_folder_id VARCHAR(200) NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_conversation_reference_user_project
            ON assistant_core.conversation_reference (user_id, native_project_id);
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_conversation_reference_user_folder
            ON assistant_core.conversation_reference (user_id, native_folder_id);
            """
        )
    )

    # 4. artifacts
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.artifacts
            ADD COLUMN IF NOT EXISTS native_project_id VARCHAR(200) NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE assistant_core.artifacts
            ADD COLUMN IF NOT EXISTS native_folder_id VARCHAR(200) NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_artifacts_user_project
            ON assistant_core.artifacts (user_id, native_project_id);
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_artifacts_user_folder
            ON assistant_core.artifacts (user_id, native_folder_id);
            """
        )
    )


def downgrade() -> None:
    """Remove native_project_id and native_folder_id columns and indexes."""
    op.execute(sa.text("SET search_path TO assistant_core, extensions, public"))

    # 1. artifacts
    op.execute(sa.text("DROP INDEX IF EXISTS assistant_core.ix_artifacts_user_folder;"))
    op.execute(sa.text("DROP INDEX IF EXISTS assistant_core.ix_artifacts_user_project;"))
    op.execute(sa.text("ALTER TABLE assistant_core.artifacts DROP COLUMN IF EXISTS native_folder_id;"))
    op.execute(sa.text("ALTER TABLE assistant_core.artifacts DROP COLUMN IF EXISTS native_project_id;"))

    # 2. conversation_reference
    op.execute(sa.text("DROP INDEX IF EXISTS assistant_core.ix_conversation_reference_user_folder;"))
    op.execute(sa.text("DROP INDEX IF EXISTS assistant_core.ix_conversation_reference_user_project;"))
    op.execute(sa.text("ALTER TABLE assistant_core.conversation_reference DROP COLUMN IF EXISTS native_folder_id;"))
    op.execute(sa.text("ALTER TABLE assistant_core.conversation_reference DROP COLUMN IF EXISTS native_project_id;"))

    # 3. completed_turn
    op.execute(sa.text("DROP INDEX IF EXISTS assistant_core.ix_completed_turn_folder;"))
    op.execute(sa.text("DROP INDEX IF EXISTS assistant_core.ix_completed_turn_project;"))
    op.execute(sa.text("ALTER TABLE assistant_core.completed_turn DROP COLUMN IF EXISTS native_folder_id;"))
    op.execute(sa.text("ALTER TABLE assistant_core.completed_turn DROP COLUMN IF EXISTS native_project_id;"))

    # 4. event_inbox
    op.execute(sa.text("DROP INDEX IF EXISTS assistant_core.ix_event_inbox_folder;"))
    op.execute(sa.text("DROP INDEX IF EXISTS assistant_core.ix_event_inbox_project;"))
    op.execute(sa.text("ALTER TABLE assistant_core.event_inbox DROP COLUMN IF EXISTS native_folder_id;"))
    op.execute(sa.text("ALTER TABLE assistant_core.event_inbox DROP COLUMN IF EXISTS native_project_id;"))
