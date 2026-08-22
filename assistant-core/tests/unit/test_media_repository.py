"""Unit tests for Media repository functions."""

import uuid
from datetime import UTC, datetime

import pytest

from assistant_core.media.models import MediaDocument, MediaSegment
from assistant_core.media.repository import (
    delete_media_document,
    get_media_document,
    get_media_document_by_url,
    list_media_documents,
    search_media,
    store_media_analysis,
    tombstone_media_document,
)
from assistant_core.media.schemas import MediaAnalysisResult, MediaSegmentAnalysis


class MockResult:
    def __init__(
        self,
        scalar: object | None = None,
        scalars_list: list[object] | None = None,
        all_list: list[object] | None = None,
        rowcount: int = 0,
    ) -> None:
        self.scalar = scalar
        self._scalars_list = scalars_list or []
        self._all_list = all_list or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> object | None:
        return self.scalar

    def scalars(self) -> "MockResult":
        return self

    def all(self) -> list[object]:
        return self._all_list or self._scalars_list


class MockAsyncSession:
    def __init__(self, results: list[MockResult] | None = None) -> None:
        self.results = list(results or [])
        self.added: list[object] = []
        self.flushed = False
        self.statements: list[object] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flushed = True

    async def execute(self, stmt: object) -> MockResult:
        self.statements.append(stmt)
        if self.results:
            return self.results.pop(0)
        return MockResult()


@pytest.mark.anyio
async def test_store_media_analysis_new_document() -> None:
    """Verify store_media_analysis creates new MediaDocument and MediaSegments."""
    user_id = uuid.uuid4()
    analysis = MediaAnalysisResult(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        media_type="youtube",
        title="Rick Astley - Never Gonna Give You Up",
        description="Music Video",
        channel_or_author="Rick Astley",
        duration_seconds=213,
        summary="A legendary music video.",
        key_takeaways=["Never gonna give you up"],
        topics=["music", "pop"],
        segments=[
            MediaSegmentAnalysis(
                segment_index=0,
                start_time_seconds=0,
                end_time_seconds=60,
                label="Verse 1",
                content="Introduction and first verse.",
            ),
            MediaSegmentAnalysis(
                segment_index=1,
                start_time_seconds=60,
                end_time_seconds=120,
                label="Chorus",
                content="Main chorus singing.",
            ),
        ],
    )

    doc_embedding = [0.1] * 1536
    seg_embeddings = [[0.2] * 1536, [0.3] * 1536]

    # First execute: select existing doc returns None
    session = MockAsyncSession([MockResult(scalar=None)])
    doc = await store_media_analysis(
        session,
        user_id=user_id,
        analysis=analysis,
        document_embedding=doc_embedding,
        segment_embeddings=seg_embeddings,
    )

    assert session.flushed is True
    assert doc.user_id == user_id
    assert doc.title == "Rick Astley - Never Gonna Give You Up"
    assert doc.total_segments == 2
    assert doc.embedding == doc_embedding

    # Verify segments were added
    segments = [item for item in session.added if isinstance(item, MediaSegment)]
    assert len(segments) == 2
    assert segments[0].label == "Verse 1"
    assert segments[0].embedding == [0.2] * 1536
    assert segments[1].label == "Chorus"
    assert segments[1].embedding == [0.3] * 1536


@pytest.mark.anyio
async def test_store_media_analysis_update_existing_document() -> None:
    """Verify store_media_analysis updates existing document and replaces segments."""
    user_id = uuid.uuid4()
    existing_doc = MediaDocument(
        id=uuid.uuid4(),
        user_id=user_id,
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        media_type="youtube",
        title="Old Title",
        summary="Old summary",
        key_takeaways=[],
        topics=[],
        total_segments=0,
    )

    analysis = MediaAnalysisResult(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        media_type="youtube",
        title="Updated Title",
        description="Updated description",
        channel_or_author="Rick Astley",
        duration_seconds=213,
        summary="Updated summary",
        key_takeaways=["Takeaway 1"],
        topics=["music"],
        segments=[
            MediaSegmentAnalysis(
                segment_index=0,
                start_time_seconds=0,
                end_time_seconds=100,
                label="Full Song",
                content="Complete performance.",
            )
        ],
    )

    # 1. select existing doc returns existing_doc
    # 2. delete old segments execute
    session = MockAsyncSession([
        MockResult(scalar=existing_doc),
        MockResult(rowcount=2),
    ])

    doc = await store_media_analysis(
        session,
        user_id=user_id,
        analysis=analysis,
    )

    assert doc.id == existing_doc.id
    assert doc.title == "Updated Title"
    assert doc.summary == "Updated summary"
    assert doc.total_segments == 1


@pytest.mark.anyio
async def test_get_media_document() -> None:
    """Verify get_media_document returns MediaDocumentDetail with segments."""
    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_doc = MediaDocument(
        id=doc_id,
        user_id=user_id,
        url="https://www.youtube.com/watch?v=abc",
        media_type="youtube",
        title="Test Video",
        description="Test Desc",
        channel_or_author="Author",
        duration_seconds=120,
        summary="Summary text",
        key_takeaways=["Takeaway 1"],
        topics=["topic1"],
        total_segments=1,
        created_at=now,
        updated_at=now,
    )

    mock_seg = MediaSegment(
        id=uuid.uuid4(),
        document_id=doc_id,
        user_id=user_id,
        segment_index=0,
        start_time_seconds=0,
        end_time_seconds=60,
        label="Chapter 1",
        content="Chapter content",
        created_at=now,
    )

    # 1. select document
    # 2. select segments
    session = MockAsyncSession([
        MockResult(scalar=mock_doc),
        MockResult(scalars_list=[mock_seg]),
    ])

    detail = await get_media_document(session, doc_id, user_id=user_id)
    assert detail is not None
    assert detail.id == doc_id
    assert detail.title == "Test Video"
    assert len(detail.segments) == 1
    assert detail.segments[0].label == "Chapter 1"


@pytest.mark.anyio
async def test_get_media_document_by_url() -> None:
    """Verify get_media_document_by_url finds document and loads details."""
    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_doc = MediaDocument(
        id=doc_id,
        user_id=user_id,
        url="https://www.youtube.com/watch?v=xyz",
        media_type="youtube",
        title="XYZ Video",
        summary="Summary",
        key_takeaways=[],
        topics=[],
        total_segments=0,
        created_at=now,
        updated_at=now,
    )

    # 1. select by url
    # 2. select doc by id (inside get_media_document)
    # 3. select segments
    session = MockAsyncSession([
        MockResult(scalar=mock_doc),
        MockResult(scalar=mock_doc),
        MockResult(scalars_list=[]),
    ])

    detail = await get_media_document_by_url(session, user_id, "https://www.youtube.com/watch?v=xyz")
    assert detail is not None
    assert detail.title == "XYZ Video"


@pytest.mark.anyio
async def test_list_media_documents() -> None:
    """Verify list_media_documents returns mapped details with pagination."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_doc = MediaDocument(
        id=uuid.uuid4(),
        user_id=user_id,
        url="https://www.youtube.com/watch?v=test",
        media_type="youtube",
        title="Listed Video",
        summary="Summary",
        key_takeaways=["Point 1"],
        topics=["tag"],
        total_segments=1,
        created_at=now,
        updated_at=now,
    )

    session = MockAsyncSession([MockResult(scalars_list=[mock_doc])])
    docs = await list_media_documents(session, user_id, limit=10, offset=0)
    assert len(docs) == 1
    assert docs[0].title == "Listed Video"


@pytest.mark.anyio
async def test_search_media() -> None:
    """Verify search_media returns ranked MediaSearchHits using RRF."""
    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()
    seg_id = uuid.uuid4()
    now = datetime.now(UTC)

    doc = MediaDocument(
        id=doc_id,
        user_id=user_id,
        url="https://www.youtube.com/watch?v=searchtest",
        media_type="youtube",
        title="PostgreSQL pgvector Tutorial",
        channel_or_author="DB Expert",
        summary="Complete tutorial on pgvector.",
        key_takeaways=[],
        topics=[],
        total_segments=1,
        created_at=now,
        updated_at=now,
    )
    seg = MediaSegment(
        id=seg_id,
        document_id=doc_id,
        user_id=user_id,
        segment_index=0,
        start_time_seconds=0,
        end_time_seconds=300,
        label="HNSW Indexes",
        content="HNSW enables approximate nearest neighbor search.",
        embedding=[0.1] * 1536,
        created_at=now,
    )

    # 1. Lexical query returns (seg, doc)
    # 2. Vector query returns (seg, doc)
    session = MockAsyncSession([
        MockResult(all_list=[(seg, doc)]),
        MockResult(all_list=[(seg, doc)]),
    ])

    hits = await search_media(
        session,
        user_id=user_id,
        query_text="pgvector hnsw",
        query_embedding=[0.1] * 1536,
        limit=5,
    )

    assert len(hits) == 1
    hit = hits[0]
    assert hit.document_id == doc_id
    assert hit.segment_id == seg_id
    assert hit.title == "PostgreSQL pgvector Tutorial"
    assert hit.match_mode == "hybrid"
    assert hit.score > 0.0


@pytest.mark.anyio
async def test_tombstone_and_delete_media_document() -> None:
    """Verify tombstone and delete functions perform expected statements."""
    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Tombstone
    session_tombstone = MockAsyncSession([MockResult(rowcount=1)])
    tombstoned = await tombstone_media_document(session_tombstone, doc_id, user_id=user_id)
    assert tombstoned is True
    assert len(session_tombstone.statements) == 1

    # Delete
    session_delete = MockAsyncSession([MockResult(rowcount=1)])
    deleted = await delete_media_document(session_delete, doc_id)
    assert deleted is True
    assert len(session_delete.statements) == 1
