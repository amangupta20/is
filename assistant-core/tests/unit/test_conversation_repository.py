"""Contracts for bounded, source-linked conversation passage persistence."""

import uuid
from datetime import UTC, datetime

import anyio
from sqlalchemy import CheckConstraint, UniqueConstraint

from assistant_core.conversation.chunking import build_turn_passages
from assistant_core.conversation.models import ConversationReference, ConversationSegment
from assistant_core.conversation.repository import (
    materialize_turn_passages,
    read_conversation_context,
    search_conversation_context,
)
from assistant_core.turns.models import CompletedTurn


class Result:
    """Return one configured scalar or row from a fake async session."""

    def __init__(
        self,
        *,
        scalar: object | None = None,
        row: tuple[object, ...] | None = None,
    ) -> None:
        self.scalar = scalar
        self.row = row

    def scalar_one_or_none(self) -> object | None:
        return self.scalar

    def one(self) -> tuple[object, ...]:
        if self.row is None:
            raise AssertionError("a configured row was required")
        return self.row


class RecordingSession:
    """Record repository statements while simulating inserts and reuse."""

    def __init__(self, results: list[Result]) -> None:
        self.results = results
        self.statements: list[object] = []

    async def execute(self, statement: object) -> Result:
        self.statements.append(statement)
        return self.results.pop(0)


def completed_turn(
    *,
    turn_id: uuid.UUID,
    chat_id: str,
    user_message_id: str,
    assistant_message_id: str,
    user_content: str,
    assistant_content: str,
) -> CompletedTurn:
    """Build one valid materialized turn without a live database."""
    return CompletedTurn(
        id=turn_id,
        event_id=f"event-{turn_id}",
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        native_chat_id=chat_id,
        native_user_message_id=user_message_id,
        native_assistant_message_id=assistant_message_id,
        user_content=user_content,
        assistant_content=assistant_content,
        user_content_sha256="a" * 64,
        assistant_content_sha256="b" * 64,
        occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


def test_turn_passages_are_bounded_role_labelled_and_deterministic() -> None:
    """Both roles retain exact provenance while oversized messages split predictably."""
    turn = completed_turn(
        turn_id=uuid.uuid4(),
        chat_id="chat-1",
        user_message_id="user-1",
        assistant_message_id="assistant-1",
        user_content="A" * 3_900 + "\n\n" + "B" * 800,
        assistant_content="Assistant finding.",
    )

    first = build_turn_passages(turn)
    second = build_turn_passages(turn)

    assert first == second
    assert [passage.role for passage in first] == ["user", "user", "assistant"]
    assert [passage.chunk_ordinal for passage in first] == [0, 1, 0]
    assert [passage.native_message_id for passage in first] == [
        "user-1",
        "user-1",
        "assistant-1",
    ]
    assert all(0 < len(passage.content) <= 4_000 for passage in first)
    assert first[0].content[-400:] == first[1].content[:400]
    assert first[2].content == "Assistant finding."
    assert all(len(passage.content_sha256) == 64 for passage in first)


def test_conversation_models_enforce_user_scoped_reuse_and_reference_identity() -> None:
    """Segments deduplicate only within one user while references retain native identity."""
    segment = ConversationSegment.__table__
    reference = ConversationReference.__table__

    segment_unique = {
        tuple(constraint.columns.keys())
        for constraint in segment.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    reference_unique = {
        tuple(constraint.columns.keys())
        for constraint in reference.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    role_checks = {
        str(constraint.sqltext)
        for constraint in reference.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert segment.schema == "assistant_core"
    assert reference.schema == "assistant_core"
    assert ("user_id", "content_sha256", "chunking_version") in segment_unique
    assert (
        "user_id",
        "native_chat_id",
        "native_message_id",
        "role",
        "chunk_ordinal",
    ) in reference_unique
    assert segment.c.embedding.type.__class__.__name__ == "VECTOR"
    assert segment.c.embedding.type.dim == 1536
    assert segment.c.embedding.nullable
    assert segment.c.search_vector.computed is not None
    assert "role IN ('user', 'assistant')" in role_checks
    assert reference.c.tombstoned_at.nullable


def test_materialization_inserts_reuses_and_replays_independent_references() -> None:
    """Exact content reuses one segment while native references remain independently idempotent."""
    user_segment_id = uuid.uuid4()
    assistant_segment_id = uuid.uuid4()
    first_turn = completed_turn(
        turn_id=uuid.uuid4(),
        chat_id="chat-1",
        user_message_id="user-1",
        assistant_message_id="assistant-1",
        user_content="Shared exact user text.",
        assistant_content="Unique assistant text.",
    )
    repeated_turn = completed_turn(
        turn_id=uuid.uuid4(),
        chat_id="chat-2",
        user_message_id="user-2",
        assistant_message_id="assistant-2",
        user_content="Shared exact user text.",
        assistant_content="",
    )
    session = RecordingSession(
        [
            Result(scalar=user_segment_id),
            Result(scalar=uuid.uuid4()),
            Result(scalar=assistant_segment_id),
            Result(scalar=uuid.uuid4()),
            Result(scalar=None),
            Result(row=(user_segment_id, "Shared exact user text.", None)),
            Result(scalar=None),
            Result(scalar=None),
            Result(row=(assistant_segment_id, "Unique assistant text.", [0.1] * 1536)),
            Result(scalar=None),
            Result(scalar=None),
            Result(row=(user_segment_id, "Shared exact user text.", None)),
            Result(scalar=uuid.uuid4()),
        ]
    )

    async def exercise() -> tuple[object, object, object]:
        inserted = await materialize_turn_passages(session, first_turn)  # type: ignore[arg-type]
        replayed = await materialize_turn_passages(session, first_turn)  # type: ignore[arg-type]
        reused = await materialize_turn_passages(session, repeated_turn)  # type: ignore[arg-type]
        return inserted, replayed, reused

    inserted, replayed, reused = anyio.run(exercise)

    assert inserted.new_segments == 2
    assert inserted.reused_segments == 0
    assert inserted.new_references == 2
    assert inserted.missing_embedding_ids == (user_segment_id, assistant_segment_id)
    assert replayed.new_segments == 0
    assert replayed.reused_segments == 2
    assert replayed.new_references == 0
    assert replayed.missing_embedding_ids == (user_segment_id,)
    assert reused.new_segments == 0
    assert reused.reused_segments == 1
    assert reused.new_references == 1
    assert reused.missing_embedding_ids == (user_segment_id,)
    assert session.results == []


def test_hybrid_search_and_read_queries_are_owner_scoped_and_active_only() -> None:
    """Lexical/vector candidates and source reads compile with tenant/tombstone guards."""
    from sqlalchemy.dialects import postgresql

    class EmptyResult:
        def all(self) -> list[object]:
            return []

        def one_or_none(self) -> None:
            return None

    class CaptureSession:
        def __init__(self) -> None:
            self.statements: list[object] = []

        async def execute(self, statement: object) -> EmptyResult:
            self.statements.append(statement)
            return EmptyResult()

    session = CaptureSession()

    async def exercise() -> tuple[object, object]:
        hits = await search_conversation_context(
            session,  # type: ignore[arg-type]
            native_user_id="native-user-1",
            query="paraphrased discussion",
            query_embedding=[0.1] * 1536,
            limit=5,
        )
        read = await read_conversation_context(
            session,  # type: ignore[arg-type]
            native_user_id="native-user-1",
            source_id=uuid.uuid4(),
        )
        return hits, read

    hits, read = anyio.run(exercise)

    assert hits == []
    assert read is None
    assert len(session.statements) == 3
    compiled = [
        statement.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
        for statement in session.statements
    ]
    sql = "\n".join(str(statement) for statement in compiled)
    parameters = [value for statement in compiled for value in statement.params.values()]
    assert "websearch_to_tsquery" in sql
    assert "<=>" in sql
    assert sql.count("conversation_reference.tombstoned_at IS NULL") == 3
    assert sql.count("completed_turn.tombstoned_at IS NULL") == 3
    assert parameters.count("native-user-1") == 3
