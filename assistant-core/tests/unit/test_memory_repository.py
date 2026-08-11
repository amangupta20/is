"""Repository contract for source-linked explicit-memory changes."""

import uuid
from datetime import UTC, datetime

import anyio
from sqlalchemy.dialects import postgresql

from assistant_core.turns.models import CompletedTurn


class ScalarResult:
    """Minimal async execute result for scalar repository queries."""

    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class RecordingSession:
    """Record statements while returning deterministic scalar values."""

    def __init__(self, values: list[object | None]) -> None:
        self.values = values
        self.statements: list[object] = []

    async def execute(self, statement: object) -> ScalarResult:
        self.statements.append(statement)
        return ScalarResult(self.values.pop(0))


def completed_turn(*, content: str, message_id: str) -> CompletedTurn:
    """Build one persisted source turn without a database fixture."""
    return CompletedTurn(
        id=uuid.uuid4(),
        event_id=f"event-{message_id}",
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        native_chat_id="chat-1",
        native_user_message_id=message_id,
        native_assistant_message_id=f"assistant-{message_id}",
        user_content=content,
        assistant_content="acknowledged",
        user_content_sha256="a" * 64,
        assistant_content_sha256="b" * 64,
        occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
    )


def test_apply_explicit_candidates_inserts_replays_and_supersedes_with_evidence() -> None:
    """A corrected explicit memory preserves both source-linked ledger entries."""
    from assistant_core.memory.repository import apply_explicit_candidates
    from assistant_core.memory.schemas import ExplicitMemoryCandidate

    first_turn = completed_turn(content="I live in Pune.", message_id="user-1")
    second_turn = completed_turn(
        content="Correction: I now live in Bengaluru.", message_id="user-2"
    )
    first_candidate = ExplicitMemoryCandidate(
        key="profile.home_city",
        category="fact",
        statement="The user lives in Pune.",
        evidence_quote="I live in Pune.",
    )
    corrected_candidate = ExplicitMemoryCandidate(
        key="profile.home_city",
        category="fact",
        statement="The user lives in Bengaluru.",
        evidence_quote="I now live in Bengaluru.",
    )
    from assistant_core.memory.models import MemoryEvidence, MemoryRecord

    original = MemoryRecord(
        id=uuid.uuid4(),
        user_id=first_turn.user_id,
        key=first_candidate.key,
        category=first_candidate.category,
        statement=first_candidate.statement,
    )
    replacement = MemoryRecord(
        id=uuid.uuid4(),
        user_id=first_turn.user_id,
        key=corrected_candidate.key,
        category=corrected_candidate.category,
        statement=corrected_candidate.statement,
    )
    session = RecordingSession(
        [None, original, None, original, original.id, original, None, replacement, None]
    )

    async def exercise() -> tuple[list[object], list[object], list[object]]:
        inserted = await apply_explicit_candidates(session, first_turn, [first_candidate])  # type: ignore[arg-type]
        replayed = await apply_explicit_candidates(session, first_turn, [first_candidate])  # type: ignore[arg-type]
        corrected = await apply_explicit_candidates(
            session, second_turn, [corrected_candidate]
        )  # type: ignore[arg-type]
        return inserted, replayed, corrected

    inserted, replayed, corrected = anyio.run(exercise)

    assert inserted == [original]
    assert replayed == []
    assert corrected == [replacement]
    first_record_insert = session.statements[1].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    first_evidence_insert = session.statements[2].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    supersession_update = session.statements[6].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    replacement_insert = session.statements[7].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    replacement_evidence_insert = session.statements[8].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]

    assert "INSERT INTO assistant_core.memory_record" in str(first_record_insert)
    assert first_record_insert.params == {
        "id": first_record_insert.params["id"],
        "user_id": first_turn.user_id,
        "key": "profile.home_city",
        "category": "fact",
        "statement": "The user lives in Pune.",
        "kind": "explicit",
        "confidence": 1,
        "state": "active",
    }
    assert "INSERT INTO assistant_core.memory_evidence" in str(first_evidence_insert)
    assert first_evidence_insert.params == {
        "id": None,
        "memory_record_id": original.id,
        "completed_turn_id": first_turn.id,
        "native_user_message_id": "user-1",
        "evidence_quote": "I live in Pune.",
    }
    assert "UPDATE assistant_core.memory_record" in str(supersession_update)
    assert supersession_update.params["state"] == "superseded"
    assert supersession_update.params["superseded_by_id"] == replacement_insert.params["id"]
    assert "superseded_at=now()" in str(supersession_update)
    assert replacement_insert.params == {
        "id": replacement_insert.params["id"],
        "user_id": second_turn.user_id,
        "key": "profile.home_city",
        "category": "fact",
        "statement": "The user lives in Bengaluru.",
        "kind": "explicit",
        "confidence": 1,
        "state": "active",
    }
    assert replacement_evidence_insert.params == {
        "id": None,
        "memory_record_id": replacement.id,
        "completed_turn_id": second_turn.id,
        "native_user_message_id": "user-2",
        "evidence_quote": "I now live in Bengaluru.",
    }
    record_constraints = {
        str(constraint.sqltext)
        for constraint in MemoryRecord.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    evidence_constraints = {
        str(constraint.sqltext)
        for constraint in MemoryEvidence.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert "char_length(statement) <= 2000" in record_constraints
    assert "char_length(evidence_quote) <= 1000" in evidence_constraints
