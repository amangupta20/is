"""Unit tests for TopicEpisode repository functions."""

import uuid
from datetime import UTC, datetime

import pytest

from assistant_core.episodes.models import TopicEpisode
from assistant_core.episodes.repository import (
    create_topic_episode,
    get_topic_episode,
    tombstone_episodes_for_chat,
)
from assistant_core.episodes.schemas import TopicEpisodeExtraction


class MockResult:
    def __init__(
        self,
        *,
        scalar: object | None = None,
        scalars_list: list[object] | None = None,
        rowcount: int = 1,
    ) -> None:
        self.scalar = scalar
        self._scalars_list = scalars_list or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> object | None:
        return self.scalar

    def scalars(self) -> "MockResult":
        return self

    def all(self) -> list[object]:
        return self._scalars_list


class MockAsyncSession:
    def __init__(self, results: list[MockResult] | None = None) -> None:
        self.results = results or []
        self.added: list[object] = []
        self.statements: list[object] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        pass

    async def execute(self, statement: object) -> MockResult:
        self.statements.append(statement)
        if self.results:
            return self.results.pop(0)
        return MockResult()


@pytest.mark.anyio
async def test_create_topic_episode_persists_model() -> None:
    """Verify create_topic_episode instantiates and flushes model."""
    session = MockAsyncSession()
    user_id = uuid.uuid4()
    extraction = TopicEpisodeExtraction(
        title="Supavisor Pooler Setup",
        topic_category="infrastructure",
        summary="Configured Supavisor tenant connection pooler for Open WebUI vector queries.",
        decisions_made=["Use port 5432 with tenant-user format"],
        open_loops=["Benchmark connection throughput"],
        key_entities=["Supavisor", "PostgreSQL", "Dokploy"],
        start_message_id="msg-1",
        end_message_id="msg-4",
    )

    episode = await create_topic_episode(
        session,
        user_id=user_id,
        native_chat_id="chat-1",
        native_project_id="proj-1",
        native_folder_id="folder-1",
        extraction=extraction,
        turn_count=2,
        embedding=[0.01] * 1536,
    )

    assert len(session.added) == 1
    assert episode.user_id == user_id
    assert episode.title == "Supavisor Pooler Setup"
    assert episode.topic_category == "infrastructure"
    assert episode.native_project_id == "proj-1"
    assert episode.native_folder_id == "folder-1"
    assert episode.turn_count == 2
    assert episode.embedding is not None and len(episode.embedding) == 1536


@pytest.mark.anyio
async def test_get_topic_episode() -> None:
    """Verify get_topic_episode returns TopicEpisodeDetail when found."""
    user_id = uuid.uuid4()
    ep_id = uuid.uuid4()
    mock_ep = TopicEpisode(
        id=ep_id,
        user_id=user_id,
        native_chat_id="chat-1",
        native_project_id=None,
        native_folder_id=None,
        title="Architecture Discussion",
        topic_category="architecture",
        summary="Summary text",
        decisions_made=["Decision 1"],
        open_loops=["Loop 1"],
        key_entities=["FastAPI"],
        start_message_id="msg-1",
        end_message_id="msg-2",
        turn_count=1,
        embedding=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = MockAsyncSession(results=[MockResult(scalar=mock_ep)])

    detail = await get_topic_episode(session, episode_id=ep_id, native_user_id="user-1")
    assert detail is not None
    assert detail.id == ep_id
    assert detail.title == "Architecture Discussion"
    assert detail.decisions_made == ["Decision 1"]


@pytest.mark.anyio
async def test_tombstone_episodes_for_chat() -> None:
    """Verify tombstone_episodes_for_chat sets tombstoned_at on matching chat episodes."""
    session = MockAsyncSession(results=[MockResult(rowcount=3)])
    user_id = uuid.uuid4()

    count = await tombstone_episodes_for_chat(session, user_id=user_id, native_chat_id="chat-1")
    assert count == 3
    assert len(session.statements) == 1
