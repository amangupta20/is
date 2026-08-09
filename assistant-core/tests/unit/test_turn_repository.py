"""Unit contracts for idempotent completed-turn materialization."""

import uuid
from datetime import UTC, datetime
from hashlib import sha256

import anyio
import pytest
from sqlalchemy.dialects import postgresql

from assistant_core.events.models import EventInbox


def digest(content: str) -> str:
    """Hash exact UTF-8 content for a canonical test payload."""
    return sha256(content.encode("utf-8")).hexdigest()


def completed_event(*, assistant_id: str = "assistant-1") -> EventInbox:
    """Build one valid completed-turn inbox row without persistence."""
    user_content = "private-user-content"
    assistant_content = "private-assistant-content"
    return EventInbox(
        event_id="event-1",
        event_type="turn.completed.v1",
        user_id=uuid.uuid4(),
        native_chat_id="chat-1",
        native_message_id=assistant_id,
        occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
        payload={
            "source": "openwebui_outlet_filter",
            "user_message": {
                "id": "user-1",
                "role": "user",
                "content": user_content,
                "sha256": digest(user_content),
                "timestamp": 1_800_000_000,
            },
            "assistant_message": {
                "id": "assistant-1",
                "role": "assistant",
                "content": assistant_content,
                "sha256": digest(assistant_content),
            },
        },
    )


class ScalarResult:
    """Minimal async execute result for INSERT ... RETURNING."""

    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class RecordingSession:
    """Record statements while returning configured scalar results."""

    def __init__(self, values: list[object | None]) -> None:
        self.values = values
        self.statements: list[object] = []

    async def execute(self, statement: object) -> ScalarResult:
        self.statements.append(statement)
        return ScalarResult(self.values.pop(0))


def test_materialization_inserts_exact_values_and_reports_replay_duplicate() -> None:
    """PostgreSQL conflict handling inserts once and reports an identical replay."""
    from assistant_core.turns.repository import materialize_completed_turn

    event = completed_event()
    session = RecordingSession([uuid.uuid4(), None])

    async def exercise() -> None:
        inserted = await materialize_completed_turn(session, event)  # type: ignore[arg-type]
        duplicate = await materialize_completed_turn(session, event)  # type: ignore[arg-type]
        assert inserted is True
        assert duplicate is False

    anyio.run(exercise)

    assert len(session.statements) == 2
    compiled = session.statements[0].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    sql = str(compiled)
    assert "INSERT INTO assistant_core.completed_turn" in sql
    assert "ON CONFLICT (event_id) DO NOTHING" in sql
    assert "RETURNING assistant_core.completed_turn.id" in sql
    values = compiled.params
    assert values["event_id"] == event.event_id
    assert values["user_id"] == event.user_id
    assert values["native_chat_id"] == event.native_chat_id
    assert values["native_user_message_id"] == "user-1"
    assert values["native_assistant_message_id"] == "assistant-1"
    assert values["user_content"] == "private-user-content"
    assert values["assistant_content"] == "private-assistant-content"
    assert values["occurred_at"] == event.occurred_at
    assert "extract_memory" not in sql


@pytest.mark.parametrize("missing_field", ["native_chat_id", "native_message_id"])
def test_materialization_requires_inbox_native_ids(missing_field: str) -> None:
    """Required native provenance cannot be omitted from the inbox envelope."""
    from assistant_core.turns.repository import (
        INVALID_TURN_PAYLOAD_ERROR,
        InvalidTurnPayloadError,
        materialize_completed_turn,
    )

    event = completed_event()
    setattr(event, missing_field, None)
    session = RecordingSession([])

    async def exercise() -> None:
        with pytest.raises(InvalidTurnPayloadError) as captured:
            await materialize_completed_turn(session, event)  # type: ignore[arg-type]
        assert str(captured.value) == INVALID_TURN_PAYLOAD_ERROR

    anyio.run(exercise)
    assert session.statements == []


def test_materialization_rejects_mismatched_assistant_id_without_content_leak() -> None:
    """Envelope/payload disagreement becomes one bounded, content-free error."""
    from assistant_core.turns.repository import (
        INVALID_TURN_PAYLOAD_ERROR,
        InvalidTurnPayloadError,
        materialize_completed_turn,
    )

    event = completed_event(assistant_id="different-assistant")
    session = RecordingSession([])

    async def exercise() -> None:
        with pytest.raises(InvalidTurnPayloadError) as captured:
            await materialize_completed_turn(session, event)  # type: ignore[arg-type]
        rendered = str(captured.value)
        assert rendered == INVALID_TURN_PAYLOAD_ERROR
        assert "private-user-content" not in rendered
        assert "private-assistant-content" not in rendered
        assert "different-assistant" not in rendered

    anyio.run(exercise)
    assert session.statements == []


def test_materialization_wraps_schema_errors_without_rendering_bad_payload() -> None:
    """Validation details and hostile values never escape the repository boundary."""
    from assistant_core.turns.repository import (
        INVALID_TURN_PAYLOAD_ERROR,
        InvalidTurnPayloadError,
        materialize_completed_turn,
    )

    event = completed_event()
    event.payload["user_message"]["content"] = "secret-invalid-content"  # type: ignore[index]
    session = RecordingSession([])

    async def exercise() -> None:
        with pytest.raises(InvalidTurnPayloadError) as captured:
            await materialize_completed_turn(session, event)  # type: ignore[arg-type]
        assert str(captured.value) == INVALID_TURN_PAYLOAD_ERROR
        assert "secret-invalid-content" not in str(captured.value)

    anyio.run(exercise)
    assert session.statements == []
