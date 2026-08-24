"""Unit tests for topic episode compilation worker job handling."""

import uuid
from datetime import UTC, datetime

import pytest

from assistant_core.episodes.extractor import (
    TopicEpisodeExtraction,
)
from assistant_core.jobs.worker import handle
from assistant_core.turns.models import CompletedTurn


class MockSession:
    def __init__(self, turns: list[CompletedTurn] | None = None) -> None:
        self.turns = turns or []
        self.added: list[object] = []
        self.committed = False
        self.rolled_back = False

    def add(self, item: object) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def flush(self) -> None:
        pass

    async def execute(self, stmt: object) -> object:
        class ExecResult:
            def scalar_one_or_none(self_inner: object) -> object | None:
                return None

            def scalars(self_inner: object) -> object:
                return self_inner

            def all(self_inner: object) -> list[CompletedTurn]:
                return self.turns

        return ExecResult()


@pytest.mark.anyio
async def test_handle_compile_topic_episodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify worker extracts and persists topic episode from uncompiled turns."""
    user_id = uuid.uuid4()
    turn = CompletedTurn(
        id=uuid.uuid4(),
        event_id="evt-1",
        user_id=user_id,
        native_chat_id="chat-abc",
        native_project_id="proj-1",
        native_folder_id=None,
        native_user_message_id="msg-u1",
        native_assistant_message_id="msg-a1",
        user_content="Let's configure Dokploy and Traefik SSL.",
        assistant_content="Here is the Traefik configuration...",
        user_content_sha256="u123",
        assistant_content_sha256="a123",
        occurred_at=datetime.now(UTC),
    )
    session = MockSession(turns=[turn])

    mock_extraction = [
        TopicEpisodeExtraction(
            title="Dokploy & Traefik SSL Setup",
            topic_category="infrastructure",
            summary="Configured Traefik SSL certificates and Dokploy reverse proxy.",
            decisions_made=["Use Let's Encrypt automated TLS via Traefik"],
            open_loops=["Configure DNS challenge for wildcard cert"],
            key_entities=["Dokploy", "Traefik", "SSL"],
            start_message_id="msg-u1",
            end_message_id="msg-a1",
        )
    ]

    class FakeExtractor:
        async def extract_episodes(self, turns: list[object]) -> list[TopicEpisodeExtraction]:
            return mock_extraction

    monkeypatch.setattr(
        "assistant_core.jobs.worker.get_episode_extractor",
        lambda *args, **kwargs: FakeExtractor(),
    )
    monkeypatch.setattr(
        "assistant_core.jobs.worker.get_conversation_embedder",
        lambda *args, **kwargs: None,
    )

    await handle(
        session,
        "compile_topic_episodes",
        {"user_id": str(user_id), "native_chat_id": "chat-abc"},
    )

    assert session.committed is True
    assert len(session.added) == 1
    added_ep = session.added[0]
    assert added_ep.title == "Dokploy & Traefik SSL Setup"
    assert added_ep.native_chat_id == "chat-abc"
    assert added_ep.turn_count == 1


def test_compile_episodes_snapshots_scope_before_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat-scope columns are read before rollback expires the turn ORM state."""
    import anyio

    from assistant_core.jobs import worker

    class FakeTurn:
        def __init__(self, project: str | None, folder: str | None) -> None:
            self.native_user_message_id = "u1"
            self.native_assistant_message_id = "a1"
            self.user_content = "hello"
            self.assistant_content = "hi"
            self.occurred_at = None
            self._expired = False
            self._project = project
            self._folder = folder

        @property
        def native_project_id(self) -> str | None:
            if self._expired:
                raise RuntimeError("simulated MissingGreenlet")
            return self._project

        @property
        def native_folder_id(self) -> str | None:
            if self._expired:
                raise RuntimeError("simulated MissingGreenlet")
            return self._folder

    turn = FakeTurn(project="proj-1", folder="fold-1")

    class Session:
        async def rollback(self) -> None:
            turn._expired = True

        async def commit(self) -> None:
            return None

    captured: dict[str, object] = {}

    async def fake_turns(*args: object, **kwargs: object) -> list[FakeTurn]:
        return [turn]

    class FakeExtractor:
        async def extract_episodes(self, _inputs: object) -> list[object]:
            from types import SimpleNamespace

            return [
                SimpleNamespace(
                    title="T",
                    topic_category="general",
                    summary="S",
                    decisions_made=[],
                    open_loops=[],
                    key_entities=[],
                    start_message_id="u1",
                    end_message_id="a1",
                )
            ]

    async def fake_create(_session: object, **kwargs: object) -> object:
        captured.update(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(id="ep-1")

    def raise_no_embedder(_settings: object = None) -> object:
        raise RuntimeError("no embedder in test")

    session = Session()
    monkeypatch.setattr(worker, "get_uncompiled_turns_for_chat", fake_turns)
    monkeypatch.setattr(worker, "get_episode_extractor", lambda: FakeExtractor())
    monkeypatch.setattr(worker, "create_topic_episode", fake_create)
    monkeypatch.setattr(worker, "get_conversation_embedder", raise_no_embedder)

    anyio.run(
        worker.handle,
        session,
        "compile_topic_episodes",
        {"user_id": "00000000-0000-0000-0000-000000000001", "native_chat_id": "chat-x"},
    )

    assert captured["native_project_id"] == "proj-1"
    assert captured["native_folder_id"] == "fold-1"
    assert captured["native_chat_id"] == "chat-x"
