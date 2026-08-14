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
        [
            None,
            original,
            None,
            original,
            original.id,
            original,
            None,
            replacement,
            None,
            None,
        ]
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
    assert len(session.statements) == 10
    first_record_insert = session.statements[1].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    first_evidence_insert = session.statements[2].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    supersession_update = session.statements[6].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    replacement_insert = session.statements[7].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    supersession_link = session.statements[8].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    replacement_evidence_insert = session.statements[9].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]

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
    assert "superseded_by_id" not in supersession_update.params
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
    assert "UPDATE assistant_core.memory_record" in str(supersession_link)
    assert supersession_link.params["superseded_by_id"] == replacement_insert.params["id"]
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


def test_search_and_read_require_live_completed_turn_evidence() -> None:
    """Tombstoned chat evidence cannot remain discoverable as explicit memory."""
    from assistant_core.memory.repository import read_explicit_memory, search_explicit_memory

    class RowsResult:
        def scalars(self) -> "RowsResult":
            return self

        def all(self) -> list[object]:
            return []

        def one_or_none(self) -> None:
            return None

    class Session:
        def __init__(self) -> None:
            self.statements: list[object] = []

        async def execute(self, statement: object) -> RowsResult:
            self.statements.append(statement)
            return RowsResult()

    session = Session()

    async def exercise() -> None:
        await search_explicit_memory(
            session,  # type: ignore[arg-type]
            native_user_id="native-user-1",
            query="direct answers",
            limit=5,
        )
        await read_explicit_memory(
            session,  # type: ignore[arg-type]
            native_user_id="native-user-1",
            memory_source_id=uuid.uuid4(),
        )

    anyio.run(exercise)

    compiled = [
        statement.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
        for statement in session.statements
    ]
    sql = "\n".join(str(statement) for statement in compiled)
    assert len(compiled) == 2
    assert sql.count("completed_turn.tombstoned_at IS NULL") == 2
    assert "EXISTS" in str(compiled[0])


class _MockResult:
    def __init__(
        self,
        *,
        rows: list[object] | None = None,
        scalar: object | None = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self) -> "_MockResult":
        return self

    def all(self) -> list[object]:
        return self._rows

    def scalar_one_or_none(self) -> object | None:
        return self._scalar

    def scalar_one(self) -> object:
        assert self._scalar is not None
        return self._scalar

    def first(self) -> object | None:
        return self._rows[0] if self._rows else None


class _MockSession:
    def __init__(self, results: list[_MockResult]) -> None:
        self.results = results
        self.statements: list[object] = []

    async def execute(self, statement: object) -> _MockResult:
        self.statements.append(statement)
        return self.results.pop(0)


def test_list_user_memories_filters_and_attaches_evidence() -> None:
    """list_user_memories filters by status and category and attaches primary evidence."""
    from assistant_core.memory.models import MemoryEvidence, MemoryRecord
    from assistant_core.memory.repository import list_user_memories

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    id1 = uuid.UUID("00000000-0000-0000-0000-000000000011")
    id2 = uuid.UUID("00000000-0000-0000-0000-000000000012")

    rec1 = MemoryRecord(
        id=id1,
        user_id=user_id,
        key="pref.response",
        category="preference",
        statement="Concise responses.",
        state="active",
        confidence=1,
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
        updated_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    rec2 = MemoryRecord(
        id=id2,
        user_id=user_id,
        key="fact.city",
        category="fact",
        statement="User lives in SF.",
        state="active",
        confidence=1,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        updated_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    turn = completed_turn(content="I want concise responses.", message_id="msg-1")
    ev1 = MemoryEvidence(
        id=uuid.uuid4(),
        memory_record_id=id1,
        completed_turn_id=turn.id,
        native_user_message_id="msg-1",
        evidence_quote="I want concise responses.",
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
    )

    session = _MockSession(
        [
            _MockResult(rows=[rec1, rec2]),
            _MockResult(rows=[(ev1, turn)]),
        ]
    )

    results = anyio.run(
        lambda: list_user_memories(
            session,  # type: ignore[arg-type]
            native_user_id="user-1",
            status="active",
            category="preference",
            limit=50,
        )
    )

    assert len(results) == 2
    assert results[0].id == str(id1)
    assert results[0].statement == "Concise responses."
    assert results[0].evidence_quote == "I want concise responses."
    assert results[0].native_chat_id == "chat-1"
    assert results[0].native_message_id == "msg-1"
    assert results[1].id == str(id2)
    assert results[1].evidence_quote is None

    compiled_query = session.statements[0].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    compiled_str = str(compiled_query)
    assert "memory_record.state =" in compiled_str
    assert "memory_record.category =" in compiled_str
    assert "ORDER BY assistant_core.memory_record.updated_at DESC, assistant_core.memory_record.created_at DESC" in compiled_str


def test_archive_user_memory_updates_state_and_archived_at() -> None:
    """archive_user_memory transitions state to archived and sets archived_at."""
    from assistant_core.memory.models import MemoryRecord
    from assistant_core.memory.repository import archive_user_memory

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    mem_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    record = MemoryRecord(
        id=mem_id,
        user_id=user_id,
        key="pref.response",
        category="preference",
        statement="Concise responses.",
        state="active",
    )
    archived_record = MemoryRecord(
        id=mem_id,
        user_id=user_id,
        key="pref.response",
        category="preference",
        statement="Concise responses.",
        state="archived",
        archived_at=datetime.now(UTC),
    )

    session = _MockSession(
        [
            _MockResult(scalar=record),
            _MockResult(scalar=archived_record),
        ]
    )

    res = anyio.run(
        lambda: archive_user_memory(
            session,  # type: ignore[arg-type]
            native_user_id="user-1",
            memory_id=mem_id,
        )
    )

    assert res is not None
    assert res.state == "archived"
    compiled_update = session.statements[1].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    assert "UPDATE assistant_core.memory_record" in str(compiled_update)
    assert compiled_update.params["state"] == "archived"

    # Test not found
    session_not_found = _MockSession([_MockResult(scalar=None)])
    res_none = anyio.run(
        lambda: archive_user_memory(
            session_not_found,  # type: ignore[arg-type]
            native_user_id="user-1",
            memory_id=mem_id,
        )
    )
    assert res_none is None


def test_update_user_memory_updates_statement() -> None:
    """update_user_memory updates the statement text and timestamp."""
    from assistant_core.memory.models import MemoryRecord
    from assistant_core.memory.repository import update_user_memory

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    mem_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    record = MemoryRecord(
        id=mem_id,
        user_id=user_id,
        key="pref.response",
        category="preference",
        statement="Old statement.",
        state="active",
    )
    updated_record = MemoryRecord(
        id=mem_id,
        user_id=user_id,
        key="pref.response",
        category="preference",
        statement="New statement.",
        state="active",
        updated_at=datetime.now(UTC),
    )

    session = _MockSession(
        [
            _MockResult(scalar=record),
            _MockResult(scalar=updated_record),
        ]
    )

    res = anyio.run(
        lambda: update_user_memory(
            session,  # type: ignore[arg-type]
            native_user_id="user-1",
            memory_id=mem_id,
            new_statement="New statement.",
        )
    )

    assert res is not None
    assert res.statement == "New statement."
    compiled_update = session.statements[1].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    assert compiled_update.params["statement"] == "New statement."

    # Test not found
    session_not_found = _MockSession([_MockResult(scalar=None)])
    res_none = anyio.run(
        lambda: update_user_memory(
            session_not_found,  # type: ignore[arg-type]
            native_user_id="user-1",
            memory_id=mem_id,
            new_statement="New statement.",
        )
    )
    assert res_none is None


def test_merge_user_memories_creates_record_copies_evidence_and_archives_sources() -> None:
    """merge_user_memories combines records and links provenance to the new memory."""
    from assistant_core.memory.models import MemoryEvidence, MemoryRecord
    from assistant_core.memory.repository import merge_user_memories

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    id1 = uuid.UUID("00000000-0000-0000-0000-000000000011")
    id2 = uuid.UUID("00000000-0000-0000-0000-000000000012")
    new_id = uuid.UUID("00000000-0000-0000-0000-000000000099")

    rec1 = MemoryRecord(
        id=id1,
        user_id=user_id,
        key="pref.response_1",
        category="preference",
        statement="Concise responses.",
        state="active",
    )
    rec2 = MemoryRecord(
        id=id2,
        user_id=user_id,
        key="pref.response_2",
        category="preference",
        statement="Use bullet points.",
        state="active",
    )
    new_rec = MemoryRecord(
        id=new_id,
        user_id=user_id,
        key="merged.m_12345678",
        category="preference",
        statement="Concise responses in bullet points.",
        state="active",
    )
    ev1 = MemoryEvidence(
        id=uuid.uuid4(),
        memory_record_id=id1,
        completed_turn_id=uuid.uuid4(),
        native_user_message_id="msg-1",
        evidence_quote="Be concise.",
    )

    session = _MockSession(
        [
            _MockResult(rows=[rec1, rec2]),
            _MockResult(scalar=new_rec),
            _MockResult(scalar=ev1),
            _MockResult(scalar=None),
            _MockResult(scalar=None),
        ]
    )

    result = anyio.run(
        lambda: merge_user_memories(
            session,  # type: ignore[arg-type]
            native_user_id="user-1",
            source_memory_ids=[id1, id2],
            target_category="preference",
            new_statement="Concise responses in bullet points.",
        )
    )

    assert result is not None
    created, source_ids = result
    assert created.id == new_id
    assert source_ids == [id1, id2]

    # Verify source archival statement has superseded_by_id
    archive_update = session.statements[4].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    assert "UPDATE assistant_core.memory_record" in str(archive_update)
    assert archive_update.params["superseded_by_id"] == new_id
    assert archive_update.params["state"] == "archived"

    # Test partial source records found (e.g. only 1 found out of 2 requested)
    session_partial = _MockSession([_MockResult(rows=[rec1])])
    res_partial = anyio.run(
        lambda: merge_user_memories(
            session_partial,  # type: ignore[arg-type]
            native_user_id="user-1",
            source_memory_ids=[id1, id2],
            target_category="preference",
            new_statement="Consolidated statement.",
        )
    )
    assert res_partial is None

