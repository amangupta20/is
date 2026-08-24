"""Repository contract for source-linked explicit-memory changes."""

import uuid
from datetime import UTC, datetime

import anyio
from sqlalchemy.dialects import postgresql

from assistant_core.memory.models import ConsolidationRun
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
        self.added: list[object] = []

    def add(self, entity: object) -> None:
        self.added.append(entity)

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
        corrected = await apply_explicit_candidates(session, second_turn, [corrected_candidate])  # type: ignore[arg-type]
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
        "valid_from": None,
        "expires_at": None,
        "temporal_tag": None,
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
        "valid_from": None,
        "expires_at": None,
        "temporal_tag": None,
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


def test_consolidate_user_memories() -> None:
    """Consolidation applies supersessions, validity updates, and reclassifications to active memories."""
    from assistant_core.memory.consolidator import (
        ConsolidationResult,
        MemoryReclassificationDecision,
        MemorySupersessionDecision,
        MemoryValidityDecision,
    )
    from assistant_core.memory.models import MemoryRecord
    from assistant_core.memory.repository import consolidate_user_memories

    id_old = uuid.uuid4()
    id_new = uuid.uuid4()
    id_temp = uuid.uuid4()
    id_reclass = uuid.uuid4()

    record_old = MemoryRecord(
        id=id_old,
        user_id=uuid.uuid4(),
        key="os",
        category="fact",
        statement="User runs Ubuntu",
        state="active",
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    record_new = MemoryRecord(
        id=id_new,
        user_id=uuid.uuid4(),
        key="os",
        category="fact",
        statement="User runs Arch Linux",
        state="active",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    record_temp = MemoryRecord(
        id=id_temp,
        user_id=uuid.uuid4(),
        key="career.datazip",
        category="project",
        statement="User applied for a job at Datazip",
        state="active",
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    record_reclass = MemoryRecord(
        id=id_reclass,
        user_id=uuid.uuid4(),
        key="infra.supabase",
        category="fact",
        statement="Runs Supabase in Dokploy",
        state="active",
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    class MockConsolidator:
        def consolidate(self, memories: list[object]) -> ConsolidationResult:
            return ConsolidationResult(
                supersessions=[
                    MemorySupersessionDecision(
                        superseded_id=id_old,
                        superseded_by_id=id_new,
                        reason="User switched Linux distributions",
                    )
                ],
                validity_updates=[
                    MemoryValidityDecision(
                        memory_id=id_temp,
                        action="set_expiration",
                        expires_at="2026-09-18T15:00:00+00:00",
                        temporal_tag="job_application",
                        reason="Active job search milestone",
                    )
                ],
                reclassifications=[
                    MemoryReclassificationDecision(
                        memory_id=id_reclass,
                        new_category="infrastructure",
                        reason="Deployment setup belongs in infrastructure",
                    )
                ],
            )

    class FakeScalarResult:
        def __init__(self, items: list[MemoryRecord], scalar: object = None) -> None:
            self._items = items
            self._scalar = scalar

        def scalars(self) -> "FakeScalarResult":
            return self

        def all(self) -> list[MemoryRecord]:
            return self._items

        def scalar_one_or_none(self) -> object:
            return self._scalar

    class FakeSession:
        def __init__(self) -> None:
            self.statements: list[object] = []
            self.added: list[object] = []
            self.call_count = 0

        async def execute(self, statement: object) -> FakeScalarResult:
            self.statements.append(statement)
            self.call_count += 1
            if self.call_count == 1:
                # user_stmt
                return FakeScalarResult([], scalar=None)
            if self.call_count == 2:
                # memory records statement
                return FakeScalarResult([record_old, record_new, record_temp, record_reclass])
            return FakeScalarResult([])

        def add(self, item: object) -> None:
            self.added.append(item)

    session = FakeSession()

    async def run_test() -> None:
        applied = await consolidate_user_memories(
            session,  # type: ignore[arg-type]
            native_user_id="user-1",
            consolidator=MockConsolidator(),  # type: ignore[arg-type]
        )
        assert len(applied) == 3
        # Supersession
        assert applied[0]["type"] == "supersession"
        assert applied[0]["superseded_id"] == str(id_old)
        assert applied[0]["superseding_statement"] == "User runs Arch Linux"
        assert record_old.state == "superseded"

        # Validity update
        assert applied[1]["type"] == "validity_update"
        assert applied[1]["temporal_tag"] == "job_application"
        assert record_temp.temporal_tag == "job_application"
        assert record_temp.expires_at is not None

        # Reclassification
        assert applied[2]["type"] == "reclassification"
        assert applied[2]["new_category"] == "infrastructure"
        assert record_reclass.category == "infrastructure"

        run_logs = [item for item in session.added if isinstance(item, ConsolidationRun)]
        assert len(run_logs) == 1
        assert run_logs[0].status == "success"
        assert run_logs[0].superseded_count == 3

    anyio.run(run_test)


def test_apply_candidates_drops_drifted_quotes_and_applies_valid() -> None:
    """Paraphrased quotes are dropped with a warning; cosmetic drift still matches."""
    from assistant_core.memory.models import MemoryRecord
    from assistant_core.memory.repository import apply_explicit_candidates
    from assistant_core.memory.schemas import ExplicitMemoryCandidate

    turn = completed_turn(content="I live in Pune.", message_id="user-1")
    drifted = ExplicitMemoryCandidate(
        key="profile.other_city",
        category="fact",
        statement="Unverifiable claim.",
        evidence_quote="I reside within the city of Pune presently",
    )
    exact = ExplicitMemoryCandidate(
        key="profile.home_city",
        category="fact",
        statement="The user lives in Pune.",
        evidence_quote="I live in Pune.",
    )
    cosmetic = ExplicitMemoryCandidate(
        key="profile.hometown_note",
        category="fact",
        statement="Pune is the user's home city.",
        evidence_quote="i  LIVE   in pune.",
    )

    rec_exact = MemoryRecord(
        id=uuid.uuid4(),
        user_id=turn.user_id,
        key=exact.key,
        category="fact",
        statement=exact.statement,
    )
    rec_cosmetic = MemoryRecord(
        id=uuid.uuid4(),
        user_id=turn.user_id,
        key=cosmetic.key,
        category="fact",
        statement=cosmetic.statement,
    )
    session = RecordingSession([None, rec_exact, None, None, rec_cosmetic, None])

    async def exercise() -> list[MemoryRecord]:
        return await apply_explicit_candidates(
            session,
            turn,
            [drifted, exact, cosmetic],  # type: ignore[arg-type]
        )

    applied = anyio.run(exercise)

    assert applied == [rec_exact, rec_cosmetic]
    assert any("INSERT INTO assistant_core.memory_evidence" in str(s) for s in session.statements)
    evidence_inserts = [s for s in session.statements if "memory_evidence" in str(s)]
    assert len(evidence_inserts) == 2


def test_apply_candidates_returns_empty_when_all_quotes_missing() -> None:
    from assistant_core.memory.repository import apply_explicit_candidates
    from assistant_core.memory.schemas import ExplicitMemoryCandidate

    turn = completed_turn(content="Totally unrelated content.", message_id="user-9")
    candidate = ExplicitMemoryCandidate(
        key="profile.home_city",
        category="fact",
        statement="The user lives in Pune.",
        evidence_quote="I live in Pune.",
    )
    session = RecordingSession([])

    async def exercise() -> list[object]:
        return await apply_explicit_candidates(session, turn, [candidate])  # type: ignore[arg-type]

    assert anyio.run(exercise) == []
    assert session.statements == []
