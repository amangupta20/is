"""Unit tests for the historical chat backfill script."""

import uuid
from datetime import UTC, datetime

import anyio
from sqlalchemy.dialects import postgresql

from assistant_core.conversation.chunking import CHUNKING_VERSION
from assistant_core.scripts.backfill_chats import (
    active_branch_messages,
    backfill_turns_for_chat,
    pair_completed_turns,
)


def _history() -> dict:
    """A branched tree whose active branch is u1 -> a1 -> u3 -> a3."""
    return {
        "currentId": "a3",
        "messages": {
            "u1": {
                "id": "u1",
                "parentId": None,
                "role": "user",
                "content": "first q",
                "timestamp": 1750000000,
            },
            "a1": {
                "id": "a1",
                "parentId": "u1",
                "role": "assistant",
                "content": "first a",
                "timestamp": 1750000060,
            },
            "u2": {
                "id": "u2",
                "parentId": "a1",
                "role": "user",
                "content": "REGEDITED FORK BRANCH",
                "timestamp": 1750000120,
            },
            "a2": {
                "id": "a2",
                "parentId": "u2",
                "role": "assistant",
                "content": "fork reply",
                "timestamp": 1750000180,
            },
            "u3": {
                "id": "u3",
                "parentId": "a1",
                "role": "user",
                "content": "second q",
                "timestamp": 1750000240,
            },
            "a3": {
                "id": "a3",
                "parentId": "u3",
                "role": "assistant",
                "content": "second a",
                "timestamp": 1750000300,
            },
            "s0": {"id": "s0", "parentId": None, "role": "system", "content": "policy"},
        },
    }


def test_active_branch_follows_current_id_chain_ignoring_forks() -> None:
    ordered = active_branch_messages(_history())

    assert [m["id"] for m in ordered] == ["u1", "a1", "u3", "a3"]
    assert all("REGEDITED" not in m["content"] for m in ordered)


def test_pair_completed_turns_pairs_user_assistant_and_skips_tails() -> None:
    pairs = pair_completed_turns(active_branch_messages(_history()))

    assert [(u["id"], a["id"]) for u, a in pairs] == [("u1", "a1"), ("u3", "a3")]

    trailing_user = [
        {"id": "u9", "parentId": None, "role": "user", "content": "unanswered", "timestamp": 5},
    ]
    assert pair_completed_turns(trailing_user) == []


class _Result:
    def __init__(self, scalar: object = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> object | None:
        return self._scalar


class _Session:
    def __init__(self, values: list[object]) -> None:
        self.values = list(values)
        self.statements: list[object] = []
        self.added: list[object] = []

    def add(self, entity: object) -> None:
        self.added.append(entity)

    async def flush(self) -> None:
        return None

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        return _Result(self.values.pop(0) if self.values else None)


def test_backfill_inserts_idempotent_turns_with_index_jobs() -> None:
    user_id = uuid.uuid4()
    session = _Session([uuid.uuid4(), None, uuid.uuid4(), None])
    fallback = datetime.now(UTC)

    async def exercise() -> tuple[int, int]:
        return await backfill_turns_for_chat(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            native_chat_id="chat-xyz",
            history=_history(),
            chat_fallback_time=fallback,
        )

    inserted, _skipped = anyio.run(exercise)

    assert inserted == 2
    turn_insert = session.statements[0].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    job_insert = session.statements[1].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    assert str(turn_insert.params["event_id"]).startswith("backfill:v1:chat-xyz:u1:a1")
    assert turn_insert.params["occurred_at"] == datetime.fromtimestamp(1750000000, tz=UTC)
    assert turn_insert.params["user_content"] == "first q"
    assert job_insert.params["kind"] == "index_conversation"
    assert str(job_insert.params["identity_key"]).endswith(f":{CHUNKING_VERSION}")
    assert str(job_insert.params["identity_key"]).startswith("conversation:")


def test_backfill_replays_are_skipped() -> None:
    user_id = uuid.uuid4()
    session = _Session([uuid.uuid4(), None, uuid.uuid4(), None])
    fallback = datetime.now(UTC)

    async def exercise() -> tuple[int, int]:
        first = await backfill_turns_for_chat(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            native_chat_id="chat-xyz",
            history=_history(),
            chat_fallback_time=fallback,
        )
        replay_session_values = [None, None]
        session.values = replay_session_values
        second = await backfill_turns_for_chat(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            native_chat_id="chat-xyz",
            history=_history(),
            chat_fallback_time=fallback,
        )
        return first, second

    (inserted, _), (replayed, skipped) = anyio.run(exercise)

    assert inserted == 2
    assert replayed == 0
    assert skipped == 2
