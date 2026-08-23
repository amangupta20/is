"""Unit tests for inferred-memory extraction, storage, profile exclusion, and lifecycle."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import anyio
import httpx
from sqlalchemy.dialects import postgresql

from assistant_core.memory.consolidator import (
    ConsolidationResult,
    MemoryPromotionDecision,
    TaskModelMemoryConsolidator,
)
from assistant_core.memory.models import ChatProfileSnapshot, ConsolidationRun, MemoryRecord
from assistant_core.turns.models import CompletedTurn


class _Result:
    """Flexible async execute result for scalar/rows/scalars repository queries."""

    def __init__(
        self,
        *,
        scalar: Any = None,
        rows: list[Any] | None = None,
    ) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    """Record statements while replaying deterministic results."""

    def __init__(self, results: list[_Result] | None = None) -> None:
        self.results = results or []
        self.statements: list[Any] = []
        self.added: list[Any] = []

    def add(self, entity: Any) -> None:
        self.added.append(entity)

    async def flush(self) -> None:
        return None

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        if self.results:
            return self.results.pop(0)
        return _Result()


def _turn(content: str, message_id: str = "user-1") -> CompletedTurn:
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
        occurred_at=datetime(2026, 8, 23, tzinfo=UTC),
    )


def _inferred_record(statement: str = "User appears to prefer terse answers.") -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        key="style.response_brevity",
        category="preference",
        statement=statement,
        kind="inferred",
        confidence=0,
        state="active",
    )


def test_extractor_caps_and_parses_inferred_candidates() -> None:
    from assistant_core.memory.extractor import TaskModelMemoryExtractor

    candidates_json = {
        "candidates": [
            {
                "key": "profile.home_city",
                "category": "fact",
                "statement": "The user lives in Pune.",
                "evidence_quote": "I live in Pune.",
                "kind": "explicit",
            },
            {
                "key": "style.response_brevity",
                "category": "preference",
                "statement": "User appears to prefer terse answers.",
                "evidence_quote": "keep it short",
                "kind": "inferred",
            },
            {
                "key": "style.code_first",
                "category": "preference",
                "statement": "User appears to prefer code-first replies.",
                "evidence_quote": "just show me the code",
                "kind": "inferred",
            },
            {
                "key": "style.extra_pattern",
                "category": "preference",
                "statement": "Over-cap inferred pattern that must be dropped.",
                "evidence_quote": "keep it short",
                "kind": "inferred",
            },
        ]
    }
    payload = {"choices": [{"message": {"content": json.dumps(candidates_json)}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    extractor = TaskModelMemoryExtractor(
        base_url="http://mock-llm.local",
        api_key="secret",
        model="gpt-4o-mini",
        timeout_seconds=5,
        transport=transport,
    )

    turn_data = _extractor_turn()
    candidates = extractor.extract(turn_data)

    kinds = [(c.key, c.kind) for c in candidates]
    assert ("profile.home_city", "explicit") in kinds
    inferred_keys = [key for key, kind in kinds if kind == "inferred"]
    assert inferred_keys == ["style.response_brevity", "style.code_first"]
    assert "style.extra_pattern" not in inferred_keys


def _extractor_turn() -> Any:
    from assistant_core.memory.extractor import CompletedTurnData

    return CompletedTurnData(
        id=uuid.uuid4(),
        user_content="I live in Pune. keep it short, just show me the code",
        assistant_content="Sure.",
        occurred_at=datetime(2026, 8, 23, tzinfo=UTC),
    )


def test_default_candidate_kind_is_explicit() -> None:
    from assistant_core.memory.schemas import ExplicitMemoryCandidate

    candidate = ExplicitMemoryCandidate(
        key="profile.editor",
        category="preference",
        statement="User prefers Neovim.",
        evidence_quote="I use Neovim daily.",
    )
    assert candidate.kind == "explicit"


def test_apply_candidates_inserts_new_inferred_record() -> None:
    from assistant_core.memory.repository import apply_explicit_candidates
    from assistant_core.memory.schemas import ExplicitMemoryCandidate

    turn = _turn("keep it short")
    candidate = ExplicitMemoryCandidate(
        key="style.response_brevity",
        category="preference",
        statement="User appears to prefer terse answers.",
        evidence_quote="keep it short",
        kind="inferred",
    )
    stored = _inferred_record()
    session = _Session([_Result(), _Result(scalar=stored)])

    async def exercise() -> list[MemoryRecord]:
        return await apply_explicit_candidates(session, turn, [candidate])  # type: ignore[arg-type]

    applied = anyio.run(exercise)

    assert applied == [stored]
    insert_stmt = session.statements[1].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    assert "INSERT INTO assistant_core.memory_record" in str(insert_stmt)
    assert insert_stmt.params["kind"] == "inferred"
    assert insert_stmt.params["confidence"] == 0
    assert "INSERT INTO assistant_core.memory_evidence" in str(session.statements[2])
    log_entry = session.added[-1]
    assert log_entry.action == "create"
    assert log_entry.new_state["kind"] == "inferred"


def test_apply_candidates_skip_inferred_when_key_already_explicit() -> None:
    from assistant_core.memory.repository import apply_explicit_candidates
    from assistant_core.memory.schemas import ExplicitMemoryCandidate

    turn = _turn("keep it short")
    confirmed = MemoryRecord(
        id=uuid.uuid4(),
        user_id=turn.user_id,
        key="style.response_brevity",
        category="preference",
        statement="User prefers terse answers.",
        kind="explicit",
        confidence=1,
        state="active",
    )
    candidate = ExplicitMemoryCandidate(
        key="style.response_brevity",
        category="preference",
        statement="User appears to prefer terse answers.",
        evidence_quote="keep it short",
        kind="inferred",
    )
    session = _Session([_Result(scalar=confirmed)])

    async def exercise() -> list[MemoryRecord]:
        return await apply_explicit_candidates(session, turn, [candidate])  # type: ignore[arg-type]

    applied = anyio.run(exercise)

    assert applied == []
    assert len(session.statements) == 1
    assert session.added == []


def test_apply_candidates_refreshes_existing_inferred_statement() -> None:
    from assistant_core.memory.repository import apply_explicit_candidates
    from assistant_core.memory.schemas import ExplicitMemoryCandidate

    turn = _turn("answers should stay extremely brief")
    active = _inferred_record(statement="User appears to prefer terse answers.")
    candidate = ExplicitMemoryCandidate(
        key="style.response_brevity",
        category="preference",
        statement="User strongly prefers very brief answers.",
        evidence_quote="extremely brief",
        kind="inferred",
    )
    session = _Session([_Result(scalar=active), _Result()])

    async def exercise() -> list[MemoryRecord]:
        return await apply_explicit_candidates(session, turn, [candidate])  # type: ignore[arg-type]

    applied = anyio.run(exercise)

    assert applied == [active]
    assert active.statement == "User strongly prefers very brief answers."
    evidence_insert = session.statements[2]
    assert "INSERT INTO assistant_core.memory_evidence" in str(evidence_insert)
    log_entry = session.added[-1]
    assert log_entry.action == "update"


def test_profile_query_excludes_inferred_records() -> None:
    from assistant_core.memory.profile import get_or_create_profile

    user_id = uuid.uuid4()
    snapshot = ChatProfileSnapshot(
        id=uuid.uuid4(),
        user_id=user_id,
        native_chat_id="chat-1",
        rendered_text="",
        source_memory_ids=[],
    )
    session = _Session(
        [
            _Result(scalar=user_id),
            _Result(),
            _Result(rows=[]),
            _Result(scalar=snapshot),
        ]
    )

    async def exercise() -> None:
        await get_or_create_profile(
            session,  # type: ignore[arg-type]
            native_user_id="user-1",
            native_chat_id="chat-1",
        )

    anyio.run(exercise)

    memory_stmt = session.statements[2].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )  # type: ignore[attr-defined]
    assert "kind = 'explicit'" in str(memory_stmt)


def test_consolidator_runs_for_single_inferred_record_and_sends_metadata() -> None:
    calls: list[dict[str, Any]] = []
    inferred_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "promotions": [
                                        {
                                            "memory_id": str(inferred_id),
                                            "reason": "Corroborated by explicit preference history",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    consolidator = TaskModelMemoryConsolidator(
        base_url="http://mock-llm.local",
        api_key="secret",
        model="gpt-4o-mini",
        transport=transport,
    )

    result = consolidator.consolidate(
        [
            {
                "id": inferred_id,
                "key": "style.response_brevity",
                "kind": "inferred",
                "evidence_count": 3,
                "category": "preference",
                "statement": "User appears to prefer terse answers.",
                "temporal_tag": None,
                "expires_at": None,
                "created_at": "2026-08-01T00:00:00+00:00",
            }
        ]
    )

    assert len(calls) == 1
    user_content = calls[0]["messages"][1]["content"]
    assert "'kind': 'inferred'" in user_content
    assert "'evidence_count': 3" in user_content
    assert result.promotions[0].memory_id == inferred_id


def test_consolidate_user_memories_applies_promotion() -> None:
    from assistant_core.identity.models import UserIdentity
    from assistant_core.memory.repository import consolidate_user_memories

    native_user_id = "user-1"
    user = UserIdentity(id=uuid.uuid4(), native_user_id=native_user_id)
    record = _inferred_record()

    class _FakeConsolidator:
        def consolidate(self, memories: list[dict[str, Any]]) -> ConsolidationResult:
            return ConsolidationResult(
                promotions=[
                    MemoryPromotionDecision(
                        memory_id=record.id,
                        reason="Repeatedly corroborated across chats",
                    )
                ]
            )

    session = _Session(
        [
            _Result(scalar=user),
            _Result(rows=[record]),
            _Result(rows=[(record.id, 3)]),
        ]
    )

    async def exercise() -> list[dict[str, Any]]:
        return await consolidate_user_memories(
            session,  # type: ignore[arg-type]
            native_user_id=native_user_id,
            consolidator=_FakeConsolidator(),  # type: ignore[arg-type]
            trigger="worker_daily",
        )

    applied = anyio.run(exercise)

    assert applied == [
        {
            "type": "promotion",
            "memory_id": str(record.id),
            "statement": record.statement,
            "reason": "Repeatedly corroborated across chats",
        }
    ]
    assert record.kind == "explicit"
    assert record.confidence == 1
    promotion_update = next(stmt for stmt in session.statements if type(stmt).__name__ == "Update")
    compiled = promotion_update.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    assert compiled.params["kind"] == "explicit"
    assert compiled.params["confidence"] == 1
    run_log = session.added[-1]
    assert isinstance(run_log, ConsolidationRun)
    assert run_log.status == "success"
    assert run_log.superseded_count == 1
    promote_log = next(
        entry for entry in session.added if getattr(entry, "action", "") == "promote"
    )
    assert promote_log.change_source == "consolidation"


def test_consolidation_run_revert_promotion_restores_inferred() -> None:
    from assistant_core.memory.repository import revert_consolidation_item

    record = _inferred_record()
    record.kind = "explicit"
    record.confidence = 1
    run = ConsolidationRun(
        id=uuid.uuid4(),
        user_id=record.user_id,
        native_user_id="user-1",
        trigger="worker_daily",
        status="success",
        memories_scanned=1,
        superseded_count=1,
        details=[
            {
                "type": "promotion",
                "memory_id": str(record.id),
                "statement": record.statement,
                "reason": "corroborated",
            }
        ],
    )
    session = _Session([_Result(scalar=run), _Result(scalar=record)])

    async def exercise() -> dict[str, Any]:
        return await revert_consolidation_item(
            session,  # type: ignore[arg-type]
            run_id=run.id,
            item_index=0,
        )

    result = anyio.run(exercise)

    assert result["success"] is True
    assert record.kind == "inferred"
    assert record.confidence == 0
    assert run.details[0]["reverted"] is True
    assert session.added[-1].action == "revert_promotion"


def test_consolidation_run_revert_discard_reactivates_record() -> None:
    from assistant_core.memory.repository import revert_consolidation_item

    record = _inferred_record()
    record.state = "archived"
    run = ConsolidationRun(
        id=uuid.uuid4(),
        user_id=record.user_id,
        native_user_id="user-1",
        trigger="worker_daily",
        status="success",
        memories_scanned=1,
        superseded_count=1,
        details=[
            {
                "type": "discard",
                "memory_id": str(record.id),
                "statement": record.statement,
                "reason": "stale pattern",
            }
        ],
    )
    session = _Session([_Result(scalar=run), _Result(scalar=record)])

    async def exercise() -> dict[str, Any]:
        return await revert_consolidation_item(
            session,  # type: ignore[arg-type]
            run_id=run.id,
            item_index=0,
        )

    result = anyio.run(exercise)

    assert result["success"] is True
    assert record.state == "active"
    assert record.archived_at is None
    assert session.added[-1].action == "revert_discard"
