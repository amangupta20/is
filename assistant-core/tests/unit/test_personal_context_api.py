"""Tests for signed, source-linked personal-context search and read."""

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Self

import anyio
import httpx
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.main import create_app
from assistant_core.memory.models import MemoryEvidence, MemoryRecord
from assistant_core.turns.models import CompletedTurn


class _Result:
    def __init__(self, *, rows: list[object] | None = None, row: object | None = None) -> None:
        self.rows = rows or []
        self.row = row

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return self.rows

    def one_or_none(self) -> object | None:
        return self.row


class _ContextSession:
    def __init__(self, records: list[MemoryRecord], read_row: tuple[Any, ...]) -> None:
        self.records = records
        self.read_row = read_row
        self.statements: list[object] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
        native_user_id = next(
            value for value in compiled.params.values() if value in {"user-1", "user-2"}
        )
        if "memory_evidence" in str(compiled):
            return _Result(row=self.read_row if native_user_id == "user-1" else None)
        return _Result(rows=[self.records[0]] if native_user_id == "user-1" else [])


async def _post(
    app: FastAPI, path: str, body: dict[str, object]
) -> httpx.Response:
    request_body = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    signature = sign_request("a" * 32, "POST", path, timestamp, request_body)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            path,
            content=request_body,
            headers={
                "content-type": "application/json",
                "x-assistant-timestamp": timestamp,
                "x-assistant-signature": signature,
            },
        )


def test_search_previews_correct_user_memory_then_read_rejects_foreign_source() -> None:
    """Lexical search returns one user's preview and read does not cross users."""
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    foreign_user_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    source_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    foreign_source_id = uuid.UUID("00000000-0000-0000-0000-000000000012")
    record = MemoryRecord(
        id=source_id,
        user_id=user_id,
        key="style.response",
        category="preference",
        statement="Use direct answers.",
    )
    foreign_record = MemoryRecord(
        id=foreign_source_id,
        user_id=foreign_user_id,
        key="style.response",
        category="preference",
        statement="Use confidential answers.",
    )
    turn = CompletedTurn(
        id=uuid.UUID("00000000-0000-0000-0000-000000000021"),
        event_id="event-1",
        user_id=user_id,
        native_chat_id="chat-1",
        native_user_message_id="message-1",
        native_assistant_message_id="assistant-1",
        user_content="I prefer direct answers.",
        assistant_content="Understood.",
        user_content_sha256="a" * 64,
        assistant_content_sha256="b" * 64,
        occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    evidence = MemoryEvidence(
        id=uuid.UUID("00000000-0000-0000-0000-000000000031"),
        memory_record_id=source_id,
        completed_turn_id=turn.id,
        native_user_message_id="message-1",
        evidence_quote="I prefer direct answers.",
    )
    session = _ContextSession([record, foreign_record], (record, evidence, turn))
    app = create_app(Settings(hmac_secret="a" * 32))
    app.state.session_factory = lambda: session

    search = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/search",
            {
                "native_user_id": "user-1",
                "native_chat_id": "chat-1",
                "native_message_id": "message-1",
                "query": " direct ",
                "limit": 10,
            },
        )
    )
    read = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/read",
            {"native_user_id": "user-1", "memory_source_id": str(source_id)},
        )
    )
    foreign_read = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/read",
            {"native_user_id": "user-2", "memory_source_id": str(source_id)},
        )
    )

    assert search.status_code == 200
    assert search.json() == {
        "mode": "lexical",
        "results": [
            {
                "memory_source_id": str(source_id),
                "category": "preference",
                "preview": "Use direct answers.",
            }
        ],
    }
    assert read.status_code == 200
    assert read.json() == {
        "memory_source_id": str(source_id),
        "statement": "Use direct answers.",
        "category": "preference",
        "evidence_quote": "I prefer direct answers.",
        "source_native_chat_id": "chat-1",
        "source_native_message_id": "message-1",
        "neighboring_available": False,
        "full_source_available": False,
    }
    assert foreign_read.status_code == 404
    assert foreign_read.json() == {"detail": "memory source not found"}
