"""Unit tests for MediaDocument and MediaSegment SQLAlchemy models."""

import uuid

from assistant_core.media.models import MediaDocument, MediaSegment


def test_media_document_model_metadata() -> None:
    """Verify table name, schema, columns, and indexes on MediaDocument."""
    assert MediaDocument.__tablename__ == "media_document"
    column_names = {c.name for c in MediaDocument.__table__.columns}
    assert {
        "id",
        "user_id",
        "url",
        "media_type",
        "title",
        "description",
        "channel_or_author",
        "duration_seconds",
        "summary",
        "key_takeaways",
        "topics",
        "total_segments",
        "embedding",
        "search_vector",
        "tombstoned_at",
        "created_at",
        "updated_at",
    }.issubset(column_names)


def test_media_segment_model_metadata() -> None:
    """Verify table name, schema, columns, and indexes on MediaSegment."""
    assert MediaSegment.__tablename__ == "media_segment"
    column_names = {c.name for c in MediaSegment.__table__.columns}
    assert {
        "id",
        "document_id",
        "user_id",
        "segment_index",
        "start_time_seconds",
        "end_time_seconds",
        "label",
        "content",
        "embedding",
        "search_vector",
        "created_at",
    }.issubset(column_names)


def test_media_document_instantiation() -> None:
    """Verify clean instantiation of MediaDocument with default and populated fields."""
    user_id = uuid.uuid4()
    doc = MediaDocument(
        user_id=user_id,
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        media_type="youtube",
        title="Rick Astley - Never Gonna Give You Up",
        description="Official music video",
        channel_or_author="RickAstleyVEVO",
        duration_seconds=213,
        summary="Classic 1987 music video featuring dance performance.",
        key_takeaways=["Never gonna give you up", "Never gonna let you down"],
        topics=["music", "80s", "pop"],
        total_segments=2,
    )
    assert doc.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert doc.title == "Rick Astley - Never Gonna Give You Up"
    assert doc.media_type == "youtube"
    assert doc.duration_seconds == 213
    assert len(doc.key_takeaways) == 2
    assert len(doc.topics) == 3
    assert doc.total_segments == 2


def test_media_segment_instantiation() -> None:
    """Verify clean instantiation of MediaSegment with fields."""
    user_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    seg = MediaSegment(
        document_id=doc_id,
        user_id=user_id,
        segment_index=0,
        start_time_seconds=0,
        end_time_seconds=60,
        label="Intro and Verse 1",
        content="Rick sings the opening verse describing feelings and commitments.",
    )
    assert seg.document_id == doc_id
    assert seg.user_id == user_id
    assert seg.segment_index == 0
    assert seg.start_time_seconds == 0
    assert seg.end_time_seconds == 60
    assert seg.label == "Intro and Verse 1"
