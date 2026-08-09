"""Contracts for durable completed-turn persistence."""

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from assistant_core.turns.models import CompletedTurn


def test_completed_turn_has_only_stable_provenance_and_content_columns() -> None:
    """The materialized turn remains focused on source content and provenance."""
    table = CompletedTurn.__table__
    columns = table.c

    assert table.schema == "assistant_core"
    assert list(columns.keys()) == [
        "id",
        "event_id",
        "user_id",
        "native_chat_id",
        "native_user_message_id",
        "native_assistant_message_id",
        "user_content",
        "assistant_content",
        "user_content_sha256",
        "assistant_content_sha256",
        "occurred_at",
        "captured_at",
        "tombstoned_at",
    ]
    assert isinstance(columns.id.type, UUID)
    assert columns.id.primary_key
    assert columns.event_id.unique and not columns.event_id.nullable
    assert isinstance(columns.event_id.type, String)
    assert columns.event_id.type.length == 200
    assert isinstance(columns.user_id.type, UUID)
    assert not columns.user_id.nullable
    assert isinstance(next(iter(columns.user_id.foreign_keys)), ForeignKey)
    assert next(iter(columns.user_id.foreign_keys)).target_fullname == (
        "assistant_core.user_identity.id"
    )
    assert columns.native_chat_id.type.length == 200
    assert columns.native_chat_id.index and not columns.native_chat_id.nullable
    assert columns.native_user_message_id.type.length == 200
    assert not columns.native_user_message_id.nullable
    assert columns.native_assistant_message_id.type.length == 200
    assert not columns.native_assistant_message_id.nullable
    assert isinstance(columns.user_content.type, Text)
    assert not columns.user_content.nullable
    assert isinstance(columns.assistant_content.type, Text)
    assert not columns.assistant_content.nullable
    assert columns.user_content_sha256.type.length == 64
    assert columns.assistant_content_sha256.type.length == 64
    assert not columns.user_content_sha256.nullable
    assert not columns.assistant_content_sha256.nullable
    assert isinstance(columns.occurred_at.type, DateTime)
    assert columns.occurred_at.type.timezone and not columns.occurred_at.nullable
    assert isinstance(columns.captured_at.type, DateTime)
    assert columns.captured_at.type.timezone and not columns.captured_at.nullable
    assert columns.captured_at.server_default is not None
    assert isinstance(columns.tombstoned_at.type, DateTime)
    assert columns.tombstoned_at.type.timezone and columns.tombstoned_at.nullable
