"""Tests for signed, source-linked personal-context search and read."""

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Self

import anyio
import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from assistant_core.api.routes.personal_context import PersonalContextSearchRequest
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
        if "ORDER BY assistant_core.memory_evidence.created_at" in str(compiled):
            return _Result(row=self.read_row if native_user_id == "user-1" else None)
        if "conversation_reference" in str(compiled):
            return _Result(row=None, rows=[])
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
    long_statement = "Use   direct   answers. " + ("Keep   details   available. " * 16)
    normalized_long_statement = " ".join(long_statement.split())
    record = MemoryRecord(
        id=source_id,
        user_id=user_id,
        key="style.response",
        category="preference",
        statement=long_statement,
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

    accepted_boundary = PersonalContextSearchRequest.model_validate(
        {"native_user_id": "user-1", "query": f" {'x' * 1_000} "}
    )
    assert accepted_boundary.query == "x" * 1_000
    with pytest.raises(ValidationError):
        PersonalContextSearchRequest.model_validate(
            {"native_user_id": "user-1", "query": f" {'x' * 1_001} "}
        )

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
                "source_id": str(source_id),
                "source_type": "memory",
                "category": "preference",
                "role": None,
                "preview": normalized_long_statement[:239] + "…",
                "source_native_chat_id": None,
                "source_native_message_id": None,
            }
        ],
    }
    preview = search.json()["results"][0]["preview"]
    assert len(preview) == 240
    assert preview.endswith("…")
    assert read.status_code == 200
    assert read.json() == {
        "source_id": str(source_id),
        "source_type": "memory",
        "content": long_statement,
        "category": "preference",
        "role": None,
        "evidence_quote": "I prefer direct answers.",
        "source_native_chat_id": "chat-1",
        "source_native_message_id": "message-1",
        "neighbors": [],
        "full_source_available": False,
    }
    assert foreign_read.status_code == 404
    assert foreign_read.json() == {"detail": "memory source not found"}


def test_hybrid_conversation_hit_reads_bounded_neighbors_and_fails_open_lexically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Semantic recall stays owner-scoped and provider failure keeps lexical search usable."""
    from assistant_core.api.routes import personal_context
    from assistant_core.conversation.embedder import (
        CONVERSATION_EMBEDDING_FAILED_ERROR,
        ConversationEmbeddingError,
    )
    from assistant_core.conversation.schemas import (
        ConversationHit,
        ConversationNeighbor,
        ConversationRead,
    )

    source_id = uuid.UUID("00000000-0000-0000-0000-000000000041")
    hit = ConversationHit(
        source_id=source_id,
        content="We chose paragraph-first chunks for reliable conversation recall.",
        role="assistant",
        native_chat_id="chat-a",
        native_message_id="assistant-a",
        occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        score=0.032,
    )
    conversation_read = ConversationRead(
        selected=hit,
        neighbors=(
            ConversationNeighbor(
                role="user",
                content="How should the assistant split long conversations?",
                native_chat_id="chat-a",
                native_message_id="user-a",
            ),
        ),
    )
    embedding_calls: list[str] = []
    fail_embedding = False

    class Embedder:
        def embed_one(self, text: str) -> list[float]:
            embedding_calls.append(text)
            if fail_embedding:
                raise ConversationEmbeddingError(CONVERSATION_EMBEDDING_FAILED_ERROR)
            return [0.25] * 1536

    async def no_memories(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def search_conversations(
        _session: object,
        *,
        native_user_id: str,
        query: str,
        query_embedding: list[float] | None,
        limit: int,
    ) -> list[ConversationHit]:
        assert query == "how did we split discussions"
        assert limit == 5
        if native_user_id != "user-1":
            return []
        assert query_embedding is None or len(query_embedding) == 1536
        return [hit]

    async def no_memory_read(*_args: object, **_kwargs: object) -> None:
        return None

    async def read_conversation(
        _session: object,
        *,
        native_user_id: str,
        source_id: uuid.UUID,
    ) -> ConversationRead | None:
        return conversation_read if native_user_id == "user-1" and source_id == hit.source_id else None

    monkeypatch.setattr(personal_context, "search_explicit_memory", no_memories)
    monkeypatch.setattr(personal_context, "search_conversation_context", search_conversations)
    monkeypatch.setattr(personal_context, "read_explicit_memory", no_memory_read)
    monkeypatch.setattr(personal_context, "read_conversation_context", read_conversation)
    monkeypatch.setattr(personal_context, "get_conversation_embedder", lambda _settings: Embedder())

    app = create_app(
        Settings(
            hmac_secret="a" * 32,
            embedding_base_url="https://embedding.example/v1",
            embedding_model="embedding-model",
        )
    )
    session = _ContextSession([], ())
    app.state.session_factory = lambda: session

    search = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/search",
            {
                "native_user_id": "user-1",
                "query": "how did we split discussions",
                "limit": 5,
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
        "mode": "hybrid",
        "results": [
            {
                "source_id": str(source_id),
                "source_type": "conversation",
                "category": "conversation_evidence",
                "role": "assistant",
                "preview": hit.content,
                "source_native_chat_id": "chat-a",
                "source_native_message_id": "assistant-a",
            }
        ],
    }
    assert read.status_code == 200
    assert read.json() == {
        "source_id": str(source_id),
        "source_type": "conversation",
        "content": hit.content,
        "category": "conversation_evidence",
        "role": "assistant",
        "evidence_quote": None,
        "source_native_chat_id": "chat-a",
        "source_native_message_id": "assistant-a",
        "neighbors": [
            {
                "role": "user",
                "content": "How should the assistant split long conversations?",
                "source_native_chat_id": "chat-a",
                "source_native_message_id": "user-a",
            }
        ],
        "full_source_available": False,
    }
    assert foreign_read.status_code == 404

    fail_embedding = True
    lexical = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/search",
            {
                "native_user_id": "user-1",
                "query": "how did we split discussions",
                "limit": 5,
            },
        )
    )
    assert lexical.status_code == 200
    assert lexical.json()["mode"] == "lexical"
    assert lexical.json()["results"][0]["source_id"] == str(source_id)
    assert embedding_calls == [
        "how did we split discussions",
        "how did we split discussions",
    ]


async def _post_with_session(
    app: FastAPI, path: str, body: dict[str, object], token: str
) -> httpx.Response:
    request_body = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            path,
            content=request_body,
            headers={
                "content-type": "application/json",
                "x-assistant-session": token,
            },
        )


def test_list_archive_update_and_merge_memories_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """List, archive, update, and merge endpoints process authenticated requests correctly."""
    from assistant_core.api.routes import personal_context
    from assistant_core.auth.session import create_session_token
    from assistant_core.memory.schemas import UserMemoryItem

    secret = "a" * 32
    app = create_app(Settings(hmac_secret=secret))

    class MockSessionContext:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    app.state.session_factory = lambda: MockSessionContext()

    item1 = UserMemoryItem(
        id="00000000-0000-0000-0000-000000000001",
        key="pref.response",
        category="preference",
        statement="Keep responses concise.",
        state="active",
        confidence=1,
        created_at=datetime(2026, 8, 11, 10, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 11, 10, 0, 0, tzinfo=UTC),
        archived_at=None,
        superseded_at=None,
        superseded_by_id=None,
        evidence_quote="Be concise.",
        native_chat_id="chat-1",
        native_message_id="msg-1",
    )

    async def mock_list(
        _session: object,
        *,
        native_user_id: str,
        status: str = "active",
        category: str | None = None,
        limit: int = 100,
    ) -> list[UserMemoryItem]:
        assert native_user_id == "user-1"
        assert status == "active"
        assert category == "preference"
        assert limit == 50
        return [item1]

    monkeypatch.setattr(personal_context, "list_user_memories", mock_list)

    # 1. List with Session token
    token = create_session_token(secret)
    list_res = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/list",
            {
                "native_user_id": "user-1",
                "status": "active",
                "category": "preference",
                "limit": 50,
            },
            token,
        )
    )
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert len(list_data["memories"]) == 1
    assert list_data["memories"][0]["id"] == item1.id
    assert list_data["memories"][0]["statement"] == "Keep responses concise."
    assert list_data["memories"][0]["evidence_quote"] == "Be concise."
    assert list_data["memories"][0]["status"] == "active"

    # 2. List with HMAC signature
    list_hmac_res = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/list",
            {
                "native_user_id": "user-1",
                "status": "active",
                "category": "preference",
                "limit": 50,
            },
        )
    )
    assert list_hmac_res.status_code == 200

    # 3. Archive memory
    mem_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    archived_record = MemoryRecord(
        id=mem_id,
        user_id=uuid.uuid4(),
        key="pref.response",
        category="preference",
        statement="Keep responses concise.",
        state="archived",
        archived_at=datetime(2026, 8, 15, 3, 30, 0, tzinfo=UTC),
    )

    async def mock_archive(
        _session: object,
        *,
        native_user_id: str,
        memory_id: uuid.UUID,
    ) -> MemoryRecord | None:
        if native_user_id == "user-1" and memory_id == mem_id:
            return archived_record
        return None

    monkeypatch.setattr(personal_context, "archive_user_memory", mock_archive)

    archive_res = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/archive",
            {"native_user_id": "user-1", "memory_id": str(mem_id)},
            token,
        )
    )
    assert archive_res.status_code == 200
    assert archive_res.json() == {
        "status": "ok",
        "archived_id": str(mem_id),
        "archived_at": "2026-08-15T03:30:00+00:00",
    }

    # Archive not found -> 404
    archive_404 = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/archive",
            {"native_user_id": "user-other", "memory_id": str(mem_id)},
            token,
        )
    )
    assert archive_404.status_code == 404
    assert archive_404.json() == {"detail": "memory record not found"}

    # 4. Update memory statement
    updated_record = MemoryRecord(
        id=mem_id,
        user_id=uuid.uuid4(),
        key="pref.response",
        category="preference",
        statement="Updated statement.",
        state="active",
    )

    async def mock_update(
        _session: object,
        *,
        native_user_id: str,
        memory_id: uuid.UUID,
        new_statement: str,
    ) -> MemoryRecord | None:
        if native_user_id == "user-1" and memory_id == mem_id:
            return updated_record
        return None

    monkeypatch.setattr(personal_context, "update_user_memory", mock_update)

    update_res = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/update",
            {
                "native_user_id": "user-1",
                "memory_id": str(mem_id),
                "new_statement": "  Updated statement.  ",
            },
            token,
        )
    )
    assert update_res.status_code == 200
    assert update_res.json() == {
        "status": "ok",
        "memory_id": str(mem_id),
        "statement": "Updated statement.",
    }

    update_404 = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/update",
            {
                "native_user_id": "user-other",
                "memory_id": str(mem_id),
                "new_statement": "Updated statement.",
            },
            token,
        )
    )
    assert update_404.status_code == 404

    # 5. Merge memories
    source_ids = [
        uuid.UUID("00000000-0000-0000-0000-000000000001"),
        uuid.UUID("00000000-0000-0000-0000-000000000002"),
    ]
    created_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    merged_rec = MemoryRecord(
        id=created_id,
        user_id=uuid.uuid4(),
        key="merged.m_12345678",
        category="preference",
        statement="Merged statement.",
        state="active",
    )

    async def mock_merge(
        _session: object,
        *,
        native_user_id: str,
        source_memory_ids: list[uuid.UUID],
        target_category: str,
        new_statement: str,
    ) -> tuple[MemoryRecord, list[uuid.UUID]] | None:
        if native_user_id == "user-1" and len(source_memory_ids) == 2:
            return merged_rec, source_ids
        return None

    monkeypatch.setattr(personal_context, "merge_user_memories", mock_merge)

    merge_res = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/merge",
            {
                "native_user_id": "user-1",
                "source_memory_ids": [str(s) for s in source_ids],
                "target_category": "preference",
                "new_statement": "Merged statement.",
            },
            token,
        )
    )
    assert merge_res.status_code == 200
    assert merge_res.json() == {
        "status": "ok",
        "created_id": str(created_id),
        "archived_source_ids": [str(s) for s in source_ids],
    }

    merge_404 = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/merge",
            {
                "native_user_id": "user-other",
                "source_memory_ids": [str(s) for s in source_ids],
                "target_category": "preference",
                "new_statement": "Merged statement.",
            },
            token,
        )
    )
    assert merge_404.status_code == 404


def test_management_endpoints_reject_unauthorized_requests() -> None:
    """Requests without valid session token or HMAC signature return 401."""
    app = create_app(Settings(hmac_secret="a" * 32))
    transport = httpx.ASGITransport(app=app)

    async def exercise() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res_list = await client.post(
                "/v1/personal-context/list",
                json={"native_user_id": "user-1"},
            )
            res_archive = await client.post(
                "/v1/personal-context/archive",
                json={"native_user_id": "user-1", "memory_id": str(uuid.uuid4())},
                headers={"x-assistant-session": "invalid-token"},
            )
            res_update = await client.post(
                "/v1/personal-context/update",
                json={
                    "native_user_id": "user-1",
                    "memory_id": str(uuid.uuid4()),
                    "new_statement": "test",
                },
                headers={"x-assistant-session": "invalid-token"},
            )
            res_merge = await client.post(
                "/v1/personal-context/merge",
                json={
                    "native_user_id": "user-1",
                    "source_memory_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
                    "new_statement": "test",
                },
            )
            return [res_list, res_archive, res_update, res_merge]

    responses = anyio.run(exercise)
    assert [res.status_code for res in responses] == [401, 401, 401, 401]


def test_management_endpoints_validation_errors() -> None:
    """Invalid requests return 422 validation errors."""
    from assistant_core.auth.session import create_session_token

    secret = "a" * 32
    app = create_app(Settings(hmac_secret=secret))
    token = create_session_token(secret)

    # 1. Invalid UUID in archive
    bad_uuid = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/archive",
            {"native_user_id": "user-1", "memory_id": "not-a-uuid"},
            token,
        )
    )
    assert bad_uuid.status_code == 422

    # 2. Blank statement in update
    blank_statement = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/update",
            {"native_user_id": "user-1", "memory_id": str(uuid.uuid4()), "new_statement": "   "},
            token,
        )
    )
    assert blank_statement.status_code == 422

    # 3. Merge with less than 2 source IDs
    few_sources = anyio.run(
        lambda: _post_with_session(
            app,
            "/v1/personal-context/merge",
            {
                "native_user_id": "user-1",
                "source_memory_ids": [str(uuid.uuid4())],
                "new_statement": "test",
            },
            token,
        )
    )
    assert few_sources.status_code == 422

