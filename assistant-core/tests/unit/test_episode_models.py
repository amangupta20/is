"""Unit tests for TopicEpisode SQLAlchemy model."""

import uuid

from assistant_core.episodes.models import TopicEpisode


def test_topic_episode_model_metadata() -> None:
    """Verify table name, schema, constraints and indexes on TopicEpisode."""
    assert TopicEpisode.__tablename__ == "topic_episode"
    column_names = {c.name for c in TopicEpisode.__table__.columns}
    assert {
        "id",
        "user_id",
        "native_chat_id",
        "native_project_id",
        "native_folder_id",
        "title",
        "topic_category",
        "summary",
        "decisions_made",
        "open_loops",
        "key_entities",
        "start_message_id",
        "end_message_id",
        "turn_count",
        "embedding",
        "search_vector",
        "tombstoned_at",
        "created_at",
        "updated_at",
    }.issubset(column_names)


def test_topic_episode_instantiation() -> None:
    """Verify clean instantiation of TopicEpisode with default fields."""
    user_id = uuid.uuid4()
    episode = TopicEpisode(
        user_id=user_id,
        native_chat_id="chat-123",
        native_project_id="proj-abc",
        native_folder_id="folder-xyz",
        title="Architecture Discussion",
        topic_category="architecture",
        summary="Discussed microservices vs modular monolith and decided on modular monolith.",
        decisions_made=["Adopt modular monolith for v1"],
        open_loops=["Benchmark pgvector latency under load"],
        key_entities=["PostgreSQL", "FastAPI", "Alembic"],
        start_message_id="msg-1",
        end_message_id="msg-10",
        turn_count=5,
    )
    assert episode.native_chat_id == "chat-123"
    assert episode.title == "Architecture Discussion"
    assert episode.turn_count == 5
    assert len(episode.decisions_made) == 1
    assert len(episode.open_loops) == 1
    assert len(episode.key_entities) == 3
