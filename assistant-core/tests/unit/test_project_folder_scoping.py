"""Unit tests for Project & Folder Context Scoping (Symmetrical 3-Tier Model)."""

import runpy
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant_core.artifacts.models import Artifact
from assistant_core.artifacts.schemas import CreateArtifactRequest
from assistant_core.conversation.models import ConversationReference
from assistant_core.conversation.schemas import ConversationHit
from assistant_core.turns.models import CompletedTurn

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def test_migration_0010_upgrade_and_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Migration 0010 adds project and folder columns with indexes and reverses cleanly."""
    migration_file = MIGRATIONS_DIR / "0010_project_folder_scoping.py"
    assert migration_file.is_file(), f"Migration file not found: {migration_file}"

    executed_sql: list[str] = []

    def fake_execute(stmt: object) -> None:
        executed_sql.append(str(stmt))

    fake_op = SimpleNamespace(
        execute=fake_execute,
    )
    monkeypatch.setattr("alembic.op", fake_op)
    namespace = runpy.run_path(str(migration_file))

    assert namespace["revision"] == "0010_project_folder_scoping"
    assert namespace["down_revision"] == "0009_artifact_binary_data"

    namespace["upgrade"]()
    assert len(executed_sql) >= 4
    combined_upgrade = " ".join(executed_sql)
    assert "native_project_id VARCHAR(200)" in combined_upgrade
    assert "native_folder_id VARCHAR(200)" in combined_upgrade
    assert "ix_event_inbox_folder" in combined_upgrade
    assert "ix_completed_turn_folder" in combined_upgrade
    assert "ix_conversation_reference_user_folder" in combined_upgrade
    assert "ix_artifacts_user_folder" in combined_upgrade

    executed_sql.clear()
    namespace["downgrade"]()
    assert len(executed_sql) >= 4
    combined_downgrade = " ".join(executed_sql)
    assert "DROP COLUMN IF EXISTS native_folder_id" in combined_downgrade
    assert "DROP COLUMN IF EXISTS native_project_id" in combined_downgrade


def test_completed_turn_and_conversation_reference_fields() -> None:
    """Models store optional native_project_id and native_folder_id."""
    turn_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(UTC)

    turn = CompletedTurn(
        id=turn_id,
        event_id="turn:v1:test",
        user_id=user_id,
        native_chat_id="chat-100",
        native_project_id="proj-stem",
        native_folder_id="folder-biology",
        native_user_message_id="msg-u1",
        native_assistant_message_id="msg-a1",
        user_content="Explain photosynthesis",
        assistant_content="Photosynthesis is...",
        user_content_sha256="abc",
        assistant_content_sha256="def",
        occurred_at=now,
    )
    assert turn.native_project_id == "proj-stem"
    assert turn.native_folder_id == "folder-biology"

    ref = ConversationReference(
        id=uuid.uuid4(),
        user_id=user_id,
        completed_turn_id=turn_id,
        segment_id=uuid.uuid4(),
        native_chat_id="chat-100",
        native_project_id="proj-stem",
        native_folder_id="folder-biology",
        native_message_id="msg-u1",
        role="user",
        role_order=0,
        chunk_ordinal=0,
        occurred_at=now,
    )
    assert ref.native_project_id == "proj-stem"
    assert ref.native_folder_id == "folder-biology"


def test_artifact_model_and_schema_project_folder_fields() -> None:
    """Artifact models and create request schemas accept project/folder IDs."""
    req = CreateArtifactRequest(
        native_user_id="user-1",
        title="Cell Structure Review",
        artifact_type="docx",
        native_project_id="proj-stem",
        native_folder_id="folder-biology",
    )
    assert req.native_project_id == "proj-stem"
    assert req.native_folder_id == "folder-biology"

    art = Artifact(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title=req.title,
        slug="cell-structure-review",
        artifact_type="docx",
        native_project_id=req.native_project_id,
        native_folder_id=req.native_folder_id,
    )
    assert art.native_project_id == "proj-stem"
    assert art.native_folder_id == "folder-biology"


def test_symmetrical_3tier_scoring_in_active_folder() -> None:
    """In an active folder, matching folder hits receive +0.15 boost, while other folders and root chats remain equal."""
    now = datetime.now(UTC)

    hit_same_folder = ConversationHit(
        source_id=uuid.uuid4(),
        content="Biology chapter 1 notes",
        role="assistant",
        native_chat_id="chat-bio-old",
        native_message_id="msg-1",
        occurred_at=now,
        score=0.10,
        native_project_id="proj-stem",
        native_folder_id="folder-bio",
    )
    hit_other_folder = ConversationHit(
        source_id=uuid.uuid4(),
        content="Tax deduction formula",
        role="assistant",
        native_chat_id="chat-tax-old",
        native_message_id="msg-2",
        occurred_at=now,
        score=0.12,  # starts with slightly higher raw lexical score
        native_project_id="proj-finance",
        native_folder_id="folder-taxes",
    )
    hit_root_chat = ConversationHit(
        source_id=uuid.uuid4(),
        content="Random thought on coffee",
        role="user",
        native_chat_id="chat-loose",
        native_message_id="msg-3",
        occurred_at=now,
        score=0.12,  # same raw lexical score as other folder
        native_project_id=None,
        native_folder_id=None,
    )

    hits = [hit_same_folder, hit_other_folder, hit_root_chat]

    target_chat_id = "chat-bio-current"
    target_folder_id = "folder-bio"
    target_project_id = "proj-stem"

    ranked: list[tuple[float, str, ConversationHit]] = []
    for hit in hits:
        score = hit.score
        if (target_folder_id and hit.native_folder_id == target_folder_id) or (
            target_project_id and hit.native_project_id == target_project_id
        ):
            score += 0.15
        elif target_chat_id and hit.native_chat_id == target_chat_id:
            score += 0.05
        ranked.append((score, str(hit.source_id), hit))

    ranked.sort(key=lambda item: -item[0])

    # 1. Same-folder hit (0.10 + 0.15 = 0.25) ranks top, beating 0.12 hits
    assert ranked[0][2].native_folder_id == "folder-bio"
    assert ranked[0][0] == pytest.approx(0.25)

    # 2. Other folder hit and root chat hit remain at exactly 0.12 (Tier 3 baseline equality)
    assert ranked[1][0] == pytest.approx(0.12)
    assert ranked[2][0] == pytest.approx(0.12)


def test_symmetrical_3tier_scoring_in_root_chat() -> None:
    """In an unfoldered/root chat, all chats (foldered or not) compete purely on base semantic score."""
    now = datetime.now(UTC)

    hit_folder_a = ConversationHit(
        source_id=uuid.uuid4(),
        content="Calculus derivatives",
        role="assistant",
        native_chat_id="chat-calc",
        native_message_id="msg-1",
        occurred_at=now,
        score=0.15,
        native_folder_id="folder-math",
    )
    hit_folder_b = ConversationHit(
        source_id=uuid.uuid4(),
        content="Physics kinematics",
        role="assistant",
        native_chat_id="chat-phys",
        native_message_id="msg-2",
        occurred_at=now,
        score=0.10,
        native_folder_id="folder-physics",
    )
    hit_root = ConversationHit(
        source_id=uuid.uuid4(),
        content="General math summary",
        role="user",
        native_chat_id="chat-root-1",
        native_message_id="msg-3",
        occurred_at=now,
        score=0.14,
        native_folder_id=None,
    )

    hits = [hit_folder_a, hit_folder_b, hit_root]

    # In a root chat with no active folder
    target_chat_id = "chat-current-root"
    target_folder_id = None
    target_project_id = None

    ranked: list[tuple[float, str, ConversationHit]] = []
    for hit in hits:
        score = hit.score
        if (target_folder_id and hit.native_folder_id == target_folder_id) or (
            target_project_id and hit.native_project_id == target_project_id
        ):
            score += 0.15
        elif target_chat_id and hit.native_chat_id == target_chat_id:
            score += 0.05
        ranked.append((score, str(hit.source_id), hit))

    ranked.sort(key=lambda item: -item[0])

    # Pure natural ranking: 0.15 (hit_folder_a) -> 0.14 (hit_root) -> 0.10 (hit_folder_b)
    assert ranked[0][2].content == "Calculus derivatives"
    assert ranked[1][2].content == "General math summary"
    assert ranked[2][2].content == "Physics kinematics"
