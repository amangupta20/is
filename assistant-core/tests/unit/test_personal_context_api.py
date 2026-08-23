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
from assistant_core.media.schemas import MediaDocumentDetail, MediaSegmentDetail
from assistant_core.memory.models import MemoryEvidence, MemoryRecord
from assistant_core.turns.models import CompletedTurn


class _Result:
    def __init__(
        self,
        *,
        rows: list[object] | None = None,
        row: object | None = None,
        scalar: object | None = None,
    ) -> None:
        self.rows = rows or []
        self.row = row
        self.scalar = scalar

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return self.rows

    def one_or_none(self) -> object | None:
        return self.row

    def scalar_one_or_none(self) -> object | None:
        if self.scalar is not None:
            return self.scalar
        if self.row is not None:
            return self.row if not isinstance(self.row, tuple) else self.row[0]
        return None


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
        if (
            "FROM assistant_core.conversation_reference" in str(compiled)
            or "FROM assistant_core.file_reference" in str(compiled)
            or "FROM assistant_core.topic_episode" in str(compiled)
            or "FROM assistant_core.media_segment" in str(compiled)
            or "FROM assistant_core.media_document" in str(compiled)
        ):
            return _Result(row=None, rows=[])
        if "SELECT assistant_core.user_identity.id FROM assistant_core.user_identity" in str(
            compiled
        ):
            return _Result(scalar=uuid.UUID("00000000-0000-0000-0000-000000000001"))
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
                "title": None,
                "decisions_made": None,
                "open_loops": None,
                "key_entities": None,
                "turn_count": None,
                "url": None,
                "media_type": None,
                "channel_or_author": None,
                "start_time_seconds": None,
                "end_time_seconds": None,
                "label": None,
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
        "memory_kind": "explicit",
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
        "title": None,
        "decisions_made": None,
        "open_loops": None,
        "key_entities": None,
        "turn_count": None,
        "full_source_available": False,
        "url": None,
        "media_type": None,
        "channel_or_author": None,
        "start_time_seconds": None,
        "end_time_seconds": None,
        "label": None,
        "key_takeaways": None,
        "topics": None,
        "neighbors": [],
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

    monkeypatch.setattr(personal_context, "search_topic_episodes", no_memories)
    monkeypatch.setattr(personal_context, "get_topic_episode", no_memory_read)

    monkeypatch.setattr(personal_context, "search_media_segments", no_memories)
    monkeypatch.setattr(personal_context, "read_media_segment", no_memory_read)
    monkeypatch.setattr(personal_context, "get_media_document", no_memory_read)
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
                "title": None,
                "decisions_made": None,
                "open_loops": None,
                "key_entities": None,
                "turn_count": None,
                "expires_at": None,
                "temporal_tag": None,
                "filename": None,
                "native_file_id": None,
                "header_path": None,
                "chunk_ordinal": None,
                "url": None,
                "media_type": None,
                "channel_or_author": None,
                "start_time_seconds": None,
                "end_time_seconds": None,
                "label": None,
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
        "memory_kind": None,
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
        "title": None,
        "decisions_made": None,
        "open_loops": None,
        "key_entities": None,
        "turn_count": None,
        "full_source_available": False,
        "url": None,
        "media_type": None,
        "channel_or_author": None,
        "start_time_seconds": None,
        "end_time_seconds": None,
        "label": None,
        "key_takeaways": None,
        "topics": None,
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
    monkeypatch.setattr(personal_context, "search_topic_episodes", no_conversations)
    monkeypatch.setattr(personal_context, "read_explicit_memory", no_memory_read)
    monkeypatch.setattr(personal_context, "read_conversation_context", no_conversation_read)
    monkeypatch.setattr(personal_context, "read_file_passage_context", mock_read_file)
    monkeypatch.setattr(personal_context, "get_topic_episode", no_memory_read)

    monkeypatch.setattr(personal_context, "search_media_segments", no_memories)
    monkeypatch.setattr(personal_context, "read_media_segment", no_memory_read)
    monkeypatch.setattr(personal_context, "get_media_document", no_memory_read)

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


def test_search_and_read_topic_episodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hybrid search returns TopicEpisode matches and read returns full formatted episode."""
    from assistant_core.api.routes import personal_context
    from assistant_core.episodes.schemas import TopicEpisodeDetail, TopicEpisodeHit

    ep_id = uuid.uuid4()
    user_id = uuid.uuid4()
    ep_hit = TopicEpisodeHit(
        episode_id=ep_id,
        native_chat_id="chat-1",
        native_project_id="proj-alpha",
        native_folder_id=None,
        title="Docker & Traefik Architecture",
        topic_category="infrastructure",
        summary="Configured Traefik reverse proxy and Let's Encrypt certificates.",
        decisions_made=["Use automated TLS via Traefik"],
        open_loops=["Configure DNS challenge"],
        key_entities=["Docker", "Traefik", "Let's Encrypt"],
        start_message_id="msg-1",
        end_message_id="msg-4",
        turn_count=2,
        score=0.98,
        match_mode="hybrid",
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
    )
    ep_detail = TopicEpisodeDetail(
        id=ep_id,
        user_id=user_id,
        native_chat_id="chat-1",
        native_project_id="proj-alpha",
        native_folder_id=None,
        title="Docker & Traefik Architecture",
        topic_category="infrastructure",
        summary="Configured Traefik reverse proxy and Let's Encrypt certificates.",
        decisions_made=["Use automated TLS via Traefik"],
        open_loops=["Configure DNS challenge"],
        key_entities=["Docker", "Traefik", "Let's Encrypt"],
        start_message_id="msg-1",
        end_message_id="msg-4",
        turn_count=2,
        has_embedding=True,
        tombstoned=False,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        updated_at=datetime(2026, 8, 22, tzinfo=UTC),
    )

    async def mock_search_episodes(*_args: object, **_kwargs: object) -> list[TopicEpisodeHit]:
        return [ep_hit]

    async def mock_get_episode(*_args: object, **_kwargs: object) -> TopicEpisodeDetail | None:
        return ep_detail

    async def no_memories(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def no_conversations(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def no_files(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def no_memory_read(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(personal_context, "search_explicit_memory", no_memories)
    monkeypatch.setattr(personal_context, "search_conversation_context", no_conversations)
    monkeypatch.setattr(personal_context, "search_file_passages", no_files)
    monkeypatch.setattr(personal_context, "search_topic_episodes", mock_search_episodes)
    monkeypatch.setattr(personal_context, "read_explicit_memory", no_memory_read)
    monkeypatch.setattr(personal_context, "get_topic_episode", mock_get_episode)

    monkeypatch.setattr(personal_context, "search_media_segments", no_memories)
    monkeypatch.setattr(personal_context, "read_media_segment", no_memory_read)
    monkeypatch.setattr(personal_context, "get_media_document", no_memory_read)

    class FakeSession:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    app = create_app(Settings(hmac_secret="a" * 32))
    app.state.session_factory = lambda: FakeSession()

    search = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/search",
            {
                "native_user_id": "user-1",
                "query": "traefik reverse proxy",
                "limit": 5,
            },
        )
    )
    assert search.status_code == 200
    results = search.json()["results"]
    assert len(results) == 1
    res = results[0]
    assert res["source_type"] == "episode"
    assert res["title"] == "Docker & Traefik Architecture"
    assert res["category"] == "infrastructure"
    assert "Configured Traefik" in res["preview"]
    assert res["decisions_made"] == ["Use automated TLS via Traefik"]

    read = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/read",
            {"native_user_id": "user-1", "memory_source_id": str(ep_id)},
        )
    )
    assert read.status_code == 200
    read_res = read.json()
    assert read_res["source_type"] == "episode"
    assert read_res["title"] == "Docker & Traefik Architecture"
    assert "# Topic Episode: Docker & Traefik Architecture" in read_res["content"]
    assert "## Decisions Made" in read_res["content"]
    assert "## Open Loops / Pending Tasks" in read_res["content"]
    assert read_res["full_source_available"] is True


def test_search_and_read_media_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hybrid search returns MediaSearchHit matches and read returns full formatted media segment."""
    from assistant_core.api.routes import personal_context
    from assistant_core.media.models import MediaDocument, MediaSegment
    from assistant_core.media.schemas import MediaSearchHit

    doc_id = uuid.uuid4()
    seg_id = uuid.uuid4()
    user_id = uuid.uuid4()

    med_hit = MediaSearchHit(
        document_id=doc_id,
        segment_id=seg_id,
        url="https://www.youtube.com/watch?v=example123",
        media_type="youtube",
        title="System Design Primer",
        channel_or_author="Tech Lead",
        start_time_seconds=60,
        end_time_seconds=180,
        label="Database Sharding",
        content="Consistent hashing allows horizontal scaling across nodes.",
        score=0.95,
        match_mode="hybrid",
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
    )

    doc_obj = MediaDocument(
        id=doc_id,
        user_id=user_id,
        url="https://www.youtube.com/watch?v=example123",
        media_type="youtube",
        title="System Design Primer",
        description="A complete guide to scaling.",
        channel_or_author="Tech Lead",
        duration_seconds=1200,
        summary="Executive overview of distributed systems design.",
        key_takeaways=["Partition data evenly", "Replicate for fault tolerance"],
        topics=["Distributed Systems", "Databases"],
        total_segments=5,
        tombstoned_at=None,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        updated_at=datetime(2026, 8, 22, tzinfo=UTC),
    )

    seg_obj = MediaSegment(
        id=seg_id,
        document_id=doc_id,
        user_id=user_id,
        segment_index=1,
        start_time_seconds=60,
        end_time_seconds=180,
        label="Database Sharding",
        content="Consistent hashing allows horizontal scaling across nodes.",
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
    )

    async def mock_search_media(*_args: object, **_kwargs: object) -> list[MediaSearchHit]:
        return [med_hit]

    async def mock_read_segment(
        *_args: object, **_kwargs: object
    ) -> tuple[MediaSegment, MediaDocument] | None:
        return seg_obj, doc_obj

    async def no_memories(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def no_read(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(personal_context, "search_explicit_memory", no_memories)
    monkeypatch.setattr(personal_context, "search_conversation_context", no_memories)
    monkeypatch.setattr(personal_context, "search_file_passages", no_memories)
    monkeypatch.setattr(personal_context, "search_topic_episodes", no_memories)
    monkeypatch.setattr(personal_context, "search_media_segments", mock_search_media)
    monkeypatch.setattr(personal_context, "read_explicit_memory", no_read)
    monkeypatch.setattr(personal_context, "get_topic_episode", no_read)
    monkeypatch.setattr(personal_context, "read_conversation_context", no_read)
    monkeypatch.setattr(personal_context, "read_file_passage_context", no_read)
    monkeypatch.setattr(personal_context, "read_media_segment", mock_read_segment)

    class FakeSession:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    app = create_app(Settings(hmac_secret="a" * 32))
    app.state.session_factory = lambda: FakeSession()

    search = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/search",
            {
                "native_user_id": "user-1",
                "query": "consistent hashing sharding",
                "limit": 5,
            },
        )
    )
    assert search.status_code == 200
    results = search.json()["results"]
    assert len(results) == 1
    res = results[0]
    assert res["source_type"] == "media"
    assert res["title"] == "System Design Primer"
    assert res["channel_or_author"] == "Tech Lead"
    assert res["start_time_seconds"] == 60
    assert res["end_time_seconds"] == 180
    assert res["label"] == "Database Sharding"
    assert "System Design Primer" in res["preview"]

    read = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/read",
            {"native_user_id": "user-1", "memory_source_id": str(seg_id)},
        )
    )
    assert read.status_code == 200
    read_res = read.json()
    assert read_res["source_type"] == "media"
    assert read_res["title"] == "System Design Primer"
    assert "# Media: System Design Primer" in read_res["content"]
    assert "## Segment Observations & Transcript" in read_res["content"]
    assert "## Key Takeaways" in read_res["content"]
    assert read_res["full_source_available"] is True


def test_media_detail_returns_full_document_or_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical-URL detail returns the entire indexed document, else found=false."""
    app = create_app(Settings(hmac_secret="a" * 32))

    media_id = uuid.uuid4()
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    detail = MediaDocumentDetail(
        id=media_id,
        user_id=user_id,
        url="https://www.youtube.com/watch?v=abc12345678",
        media_type="youtube",
        title="Real Video Title",
        channel_or_author="Real Channel",
        duration_seconds=540,
        summary="Actual content summary.",
        key_takeaways=["Fact one"],
        topics=["testing"],
        total_segments=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        segments=[
            MediaSegmentDetail(
                id=uuid.uuid4(),
                document_id=media_id,
                user_id=user_id,
                segment_index=0,
                start_time_seconds=0,
                end_time_seconds=540,
                label="Whole video",
                content="Verbatim-ish transcript span.",
                created_at=datetime.now(UTC),
            )
        ],
    )

    async def fake_get(*args: object, **kwargs: object) -> MediaDocumentDetail | None:
        return detail

    async def fake_none(*args: object, **kwargs: object) -> None:
        return None

    target = "assistant_core.media.repository.get_media_document_by_url"
    monkeypatch.setattr(target, fake_get)

    class _UserIdSession:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def execute(self, _statement: object) -> _Result:
            return _Result(scalar=user_id)

    app.state.session_factory = lambda: _UserIdSession()

    response = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/media-detail",
            {
                "native_user_id": "user-1",
                "url": "https://youtu.be/abc12345678?si=xyz",
            },
        )
    )
    assert response.status_code == 200
    data = response.json()
    assert data["found"] is True
    assert data["url"] == "https://www.youtube.com/watch?v=abc12345678"
    assert data["title"] == "Real Video Title"
    assert data["channel_or_author"] == "Real Channel"
    assert data["duration_seconds"] == 540
    assert data["segments"][0]["content"] == "Verbatim-ish transcript span."

    monkeypatch.setattr(target, fake_none)
    missing = anyio.run(
        lambda: _post(
            app,
            "/v1/personal-context/media-detail",
            {"native_user_id": "user-1", "url": "https://youtu.be/abc12345678"},
        )
    )
    assert missing.status_code == 200
    assert missing.json() == {
        "found": False,
        "url": "https://www.youtube.com/watch?v=abc12345678",
        "media_id": None,
        "title": None,
        "channel_or_author": None,
        "duration_seconds": None,
        "summary": None,
        "key_takeaways": [],
        "topics": [],
        "segments": [],
    }
