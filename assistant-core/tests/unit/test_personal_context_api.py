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
        if "conversation_reference" in str(compiled) or "file_reference" in str(compiled):
            return _Result(row=None, rows=[])
        return _Result(rows=[self.records[0]] if native_user_id == "user-1" else [])


async def _post(app: FastAPI, path: str, body: dict[str, object]) -> httpx.Response:
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
                "source_native_project_id": None,
                "source_native_folder_id": None,
                "valid_from": None,
                "expires_at": None,
                "temporal_tag": None,
                "filename": None,
                "native_file_id": None,
                "header_path": None,
                "chunk_ordinal": None,
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
        "source_native_project_id": None,
        "source_native_folder_id": None,
        "valid_from": None,
        "expires_at": None,
        "temporal_tag": None,
        "filename": None,
        "native_file_id": None,
        "header_path": None,
        "chunk_ordinal": None,
        "previous_chunk": None,
        "next_chunk": None,
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
        return (
            conversation_read if native_user_id == "user-1" and source_id == hit.source_id else None
        )

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
                "source_native_project_id": None,
                "source_native_folder_id": None,
                "valid_from": None,
                "expires_at": None,
                "temporal_tag": None,
                "filename": None,
                "native_file_id": None,
                "header_path": None,
                "chunk_ordinal": None,
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
        "source_native_project_id": None,
        "source_native_folder_id": None,
        "valid_from": None,
        "expires_at": None,
        "temporal_tag": None,
        "filename": None,
        "native_file_id": None,
        "header_path": None,
        "chunk_ordinal": None,
        "previous_chunk": None,
        "next_chunk": None,
        "neighbors": [
            {
                "role": "user",
                "content": "How should the assistant split long conversations?",
                "source_native_chat_id": "chat-a",
                "source_native_message_id": "user-a",
                "source_native_project_id": None,
                "source_native_folder_id": None,
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


def test_search_and_read_file_passages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hybrid search merges file passages and read returns surrounding file chunks."""
    from assistant_core.api.routes import personal_context
    from assistant_core.files.schemas import FileHit, FilePassageContext

    ref_id = uuid.uuid4()
    seg_id = uuid.uuid4()
    user_id = uuid.uuid4()

    file_hit = FileHit(
        reference_id=str(ref_id),
        segment_id=str(seg_id),
        native_file_id="file-xyz",
        filename="system-architecture.pdf",
        header_path="Infrastructure > Storage",
        chunk_ordinal=2,
        content="PostgreSQL pgvector storage details.",
        lexical_rank=1,
        score=0.95,
    )

    file_ctx = FilePassageContext(
        reference_id=str(ref_id),
        native_file_id="file-xyz",
        filename="system-architecture.pdf",
        mime_type="application/pdf",
        header_path="Infrastructure > Storage",
        chunk_ordinal=2,
        content="PostgreSQL pgvector storage details.",
        previous_content="Previous chunk content.",
        next_content="Next chunk content.",
    )

    async def mock_search_files(*_args: object, **_kwargs: object) -> list[FileHit]:
        return [file_hit]

    async def mock_read_file(*_args: object, **_kwargs: object) -> FilePassageContext | None:
        return file_ctx

    async def no_memories(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def no_conversations(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def no_memory_read(*_args: object, **_kwargs: object) -> None:
        return None

    async def no_conversation_read(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(personal_context, "search_explicit_memory", no_memories)
    monkeypatch.setattr(personal_context, "search_conversation_context", no_conversations)
    monkeypatch.setattr(personal_context, "search_file_passages", mock_search_files)
    monkeypatch.setattr(personal_context, "read_explicit_memory", no_memory_read)
    monkeypatch.setattr(personal_context, "read_conversation_context", no_conversation_read)
    monkeypatch.setattr(personal_context, "read_file_passage_context", mock_read_file)

    class FakeSession:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def execute(self, statement: object) -> _Result:
            return _Result(row=user_id)

    app = create_app(Settings(hmac_secret="a" * 32))
    app.state.session_factory = lambda: FakeSession()

    search = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/search",
            {
                "native_user_id": "user-1",
                "query": "pgvector storage",
                "limit": 5,
            },
        )
    )
    assert search.status_code == 200
    search_data = search.json()
    assert len(search_data["results"]) == 1
    res = search_data["results"][0]
    assert res["source_type"] == "file"
    assert res["filename"] == "system-architecture.pdf"
    assert res["header_path"] == "Infrastructure > Storage"

    read = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/read",
            {"native_user_id": "user-1", "memory_source_id": str(ref_id)},
        )
    )
    assert read.status_code == 200
    read_data = read.json()
    assert read_data["source_type"] == "file"
    assert read_data["filename"] == "system-architecture.pdf"
    assert read_data["previous_chunk"] == "Previous chunk content."
    assert read_data["next_chunk"] == "Next chunk content."
    assert read_data["full_source_available"] is True


def test_read_full_document_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconstructing the full document succeeds and fails for unknown files."""
    from assistant_core.api.routes import personal_context
    from assistant_core.files.schemas import FullFileContent

    full_doc = FullFileContent(
        native_file_id="file-123",
        filename="overview.md",
        mime_type="text/markdown",
        total_chunks=3,
        total_characters=150,
        content="# Section 1\nContent 1\n\n# Section 2\nContent 2",
    )

    async def mock_get_full_file(
        _session: object,
        *,
        file_id_or_name: str,
        native_user_id: str | None = None,
        **_kwargs: object,
    ) -> FullFileContent | None:
        if native_user_id == "user-1" and file_id_or_name in {"file-123", "overview.md"}:
            return full_doc
        return None

    monkeypatch.setattr(personal_context, "get_full_file_content", mock_get_full_file)

    class FakeSession:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    app = create_app(Settings(hmac_secret="a" * 32))
    app.state.session_factory = lambda: FakeSession()

    success = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/file-content",
            {"native_user_id": "user-1", "file_id_or_name": "overview.md"},
        )
    )
    assert success.status_code == 200
    data = success.json()
    assert data["filename"] == "overview.md"
    assert data["total_chunks"] == 3
    assert "# Section 1" in data["content"]

    missing = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/file-content",
            {"native_user_id": "user-1", "file_id_or_name": "nonexistent.pdf"},
        )
    )
    assert missing.status_code == 404
