"""Unit tests for transactional, idempotent event ingestion."""

import uuid
from datetime import UTC, datetime
from typing import Any

import anyio
from sqlalchemy.dialects import postgresql

from assistant_core.events.schemas import EventEnvelope
from assistant_core.events.service import ingest_event


class ScalarResult:
    """Small result stub exposing SQLAlchemy scalar result methods."""

    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class RecordingSession:
    """Capture executed PostgreSQL statements and reject internal commits."""

    def __init__(self, results: list[ScalarResult]) -> None:
        self.results = iter(results)
        self.statements: list[Any] = []
        self.commit_calls = 0

    async def execute(self, statement: Any) -> ScalarResult:
        self.statements.append(statement)
        return next(self.results)

    async def commit(self) -> None:
        self.commit_calls += 1


def event() -> EventEnvelope:
    """Build one valid event for service behavior tests."""
    return EventEnvelope(
        schema_version=1,
        event_id="event-1",
        event_type="chat.completed",
        native_user_id="user-1",
        native_chat_id="chat-1",
        native_message_id="message-1",
        occurred_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        payload={"source": "openwebui"},
    )


def rendered_sql(session: RecordingSession) -> list[str]:
    """Compile captured statements with the PostgreSQL dialect."""
    dialect = postgresql.dialect()
    return [str(statement.compile(dialect=dialect)) for statement in session.statements]


def test_new_event_upserts_identity_inserts_inbox_and_enqueues_one_job() -> None:
    """A new inbox row drives one process-event job without committing the transaction."""
    identity_id = uuid.uuid4()
    session = RecordingSession(
        [ScalarResult(identity_id), ScalarResult("event-1"), ScalarResult(None)]
    )

    duplicate = anyio.run(ingest_event, session, event())  # type: ignore[arg-type]

    sql = rendered_sql(session)
    assert duplicate is False
    assert len(sql) == 3
    assert "ON CONFLICT (native_user_id) DO UPDATE" in sql[0]
    assert "ON CONFLICT (event_id) DO NOTHING" in sql[1]
    assert "ON CONFLICT (identity_key) DO NOTHING" in sql[2]
    assert session.statements[2].compile().params["identity_key"] == "event:event-1"
    assert session.statements[2].compile().params["kind"] == "process_event"
    assert session.statements[2].compile().params["payload"] == {"event_id": "event-1"}
    assert session.commit_calls == 0


def test_duplicate_event_stops_before_job_creation() -> None:
    """An existing event ID reports duplicate and does not attempt another job insert."""
    session = RecordingSession([ScalarResult(uuid.uuid4()), ScalarResult(None)])

    duplicate = anyio.run(ingest_event, session, event())  # type: ignore[arg-type]

    assert duplicate is True
    assert len(session.statements) == 2
    assert session.commit_calls == 0
