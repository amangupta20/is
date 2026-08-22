"""Repository for MediaDocument and MediaSegment persistence and hybrid search."""

import uuid
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.identity.models import UserIdentity
from assistant_core.media.models import MediaDocument, MediaSegment
from assistant_core.media.schemas import (
    MediaAnalysisResult,
    MediaDocumentDetail,
    MediaSearchHit,
    MediaSegmentDetail,
)

RRF_K = 60
MEDIA_CANDIDATE_LIMIT = 20


async def store_media_analysis(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    analysis: MediaAnalysisResult,
    document_embedding: list[float] | None = None,
    segment_embeddings: list[list[float] | None] | None = None,
) -> MediaDocument:
    """Persist media analysis result, updating existing document if URL already exists for user."""
    # Check for existing document for this user & url
    stmt = (
        select(MediaDocument)
        .where(
            MediaDocument.user_id == user_id,
            MediaDocument.url == analysis.url,
        )
        .order_by(MediaDocument.created_at.desc())
        .limit(1)
    )
    existing_doc = (await session.execute(stmt)).scalar_one_or_none()

    if existing_doc is not None:
        doc = existing_doc
        doc.media_type = analysis.media_type
        doc.title = analysis.title
        doc.description = analysis.description
        doc.channel_or_author = analysis.channel_or_author
        doc.duration_seconds = analysis.duration_seconds
        doc.summary = analysis.summary
        doc.key_takeaways = analysis.key_takeaways
        doc.topics = analysis.topics
        doc.total_segments = len(analysis.segments)
        doc.tombstoned_at = None
        if document_embedding is not None:
            doc.embedding = document_embedding

        # Delete old segments
        await session.execute(
            sa_delete(MediaSegment).where(MediaSegment.document_id == doc.id)
        )
    else:
        doc = MediaDocument(
            id=uuid.uuid4(),
            user_id=user_id,
            url=analysis.url,
            media_type=analysis.media_type,
            title=analysis.title,
            description=analysis.description,
            channel_or_author=analysis.channel_or_author,
            duration_seconds=analysis.duration_seconds,
            summary=analysis.summary,
            key_takeaways=analysis.key_takeaways,
            topics=analysis.topics,
            total_segments=len(analysis.segments),
            embedding=document_embedding,
        )
        session.add(doc)

    await session.flush()

    # Insert segments
    for idx, seg in enumerate(analysis.segments):
        seg_emb = (
            segment_embeddings[idx]
            if segment_embeddings is not None and idx < len(segment_embeddings)
            else None
        )
        segment_row = MediaSegment(
            id=uuid.uuid4(),
            document_id=doc.id,
            user_id=user_id,
            segment_index=seg.segment_index if seg.segment_index is not None else idx,
            start_time_seconds=seg.start_time_seconds,
            end_time_seconds=seg.end_time_seconds,
            label=seg.label,
            content=seg.content,
            embedding=seg_emb,
        )
        session.add(segment_row)

    await session.flush()
    return doc


async def get_media_document(
    session: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    native_user_id: str | None = None,
) -> MediaDocumentDetail | None:
    """Fetch active MediaDocument by ID and include its segments."""
    if user_id is None and native_user_id is not None:
        user_stmt = select(UserIdentity.id).where(UserIdentity.native_user_id == native_user_id)
        user_id = (await session.execute(user_stmt)).scalar_one_or_none()
        if user_id is None:
            return None

    conditions = [
        MediaDocument.id == document_id,
        MediaDocument.tombstoned_at.is_(None),
    ]
    if user_id is not None:
        conditions.append(MediaDocument.user_id == user_id)

    doc_stmt = select(MediaDocument).where(*conditions)
    doc = (await session.execute(doc_stmt)).scalar_one_or_none()
    if doc is None:
        return None

    seg_stmt = (
        select(MediaSegment)
        .where(MediaSegment.document_id == doc.id)
        .order_by(MediaSegment.segment_index.asc())
    )
    segments = list((await session.execute(seg_stmt)).scalars().all())

    segment_details = [
        MediaSegmentDetail(
            id=s.id,
            document_id=s.document_id,
            user_id=s.user_id,
            segment_index=s.segment_index,
            start_time_seconds=s.start_time_seconds,
            end_time_seconds=s.end_time_seconds,
            label=s.label,
            content=s.content,
            has_embedding=s.embedding is not None,
            created_at=s.created_at,
        )
        for s in segments
    ]

    return MediaDocumentDetail(
        id=doc.id,
        user_id=doc.user_id,
        url=doc.url,
        media_type=doc.media_type,
        title=doc.title,
        description=doc.description,
        channel_or_author=doc.channel_or_author,
        duration_seconds=doc.duration_seconds,
        summary=doc.summary,
        key_takeaways=doc.key_takeaways or [],
        topics=doc.topics or [],
        total_segments=doc.total_segments,
        has_embedding=doc.embedding is not None,
        tombstoned=doc.tombstoned_at is not None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        segments=segment_details,
    )


async def get_media_document_by_url(
    session: AsyncSession,
    user_id: uuid.UUID,
    url: str,
) -> MediaDocumentDetail | None:
    """Fetch active MediaDocument by user ID and URL."""
    doc_stmt = (
        select(MediaDocument)
        .where(
            MediaDocument.user_id == user_id,
            MediaDocument.url == url,
            MediaDocument.tombstoned_at.is_(None),
        )
        .order_by(MediaDocument.created_at.desc())
        .limit(1)
    )
    doc = (await session.execute(doc_stmt)).scalar_one_or_none()
    if doc is None:
        return None

    return await get_media_document(session, doc.id, user_id=user_id)


async def list_media_documents(
    session: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    include_tombstoned: bool = False,
) -> list[MediaDocumentDetail]:
    """List media documents for user with paging."""
    conditions = [MediaDocument.user_id == user_id]
    if not include_tombstoned:
        conditions.append(MediaDocument.tombstoned_at.is_(None))

    stmt = (
        select(MediaDocument)
        .where(*conditions)
        .order_by(MediaDocument.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    docs = list((await session.execute(stmt)).scalars().all())

    results: list[MediaDocumentDetail] = []
    for doc in docs:
        results.append(
            MediaDocumentDetail(
                id=doc.id,
                user_id=doc.user_id,
                url=doc.url,
                media_type=doc.media_type,
                title=doc.title,
                description=doc.description,
                channel_or_author=doc.channel_or_author,
                duration_seconds=doc.duration_seconds,
                summary=doc.summary,
                key_takeaways=doc.key_takeaways or [],
                topics=doc.topics or [],
                total_segments=doc.total_segments,
                has_embedding=doc.embedding is not None,
                tombstoned=doc.tombstoned_at is not None,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
                segments=[],
            )
        )
    return results


async def search_media_segments(
    session: AsyncSession,
    *,
    native_user_id: str | None = None,
    user_id: uuid.UUID | None = None,
    query_text: str,
    query_embedding: list[float] | None = None,
    limit: int = 5,
) -> list[MediaSearchHit]:
    """Search media segments and documents using hybrid FTS and vector cosine similarity with RRF."""
    if user_id is None and native_user_id is not None:
        user_stmt = select(UserIdentity.id).where(UserIdentity.native_user_id == native_user_id)
        user_id = (await session.execute(user_stmt)).scalar_one_or_none()
        if user_id is None:
            return []
    if user_id is None:
        return []
    lexical_stmt = (
        select(MediaSegment, MediaDocument)
        .join(MediaDocument, MediaSegment.document_id == MediaDocument.id)
        .where(
            MediaSegment.user_id == user_id,
            MediaDocument.tombstoned_at.is_(None),
            MediaSegment.search_vector.op("@@")(func.plainto_tsquery("english", query_text)),
        )
        .order_by(
            func.ts_rank_cd(
                MediaSegment.search_vector, func.plainto_tsquery("english", query_text)
            ).desc(),
            MediaSegment.created_at.desc(),
        )
        .limit(MEDIA_CANDIDATE_LIMIT)
    )
    lexical_rows = list((await session.execute(lexical_stmt)).all())

    # 2. Vector candidate query on MediaSegment
    vector_rows: list[Any] = []
    if query_embedding is not None and len(query_embedding) == 1536:
        vector_stmt = (
            select(MediaSegment, MediaDocument)
            .join(MediaDocument, MediaSegment.document_id == MediaDocument.id)
            .where(
                MediaSegment.user_id == user_id,
                MediaDocument.tombstoned_at.is_(None),
                MediaSegment.embedding.is_not(None),
            )
            .order_by(
                MediaSegment.embedding.cosine_distance(query_embedding),
                MediaSegment.created_at.desc(),
            )
            .limit(MEDIA_CANDIDATE_LIMIT)
        )
        vector_rows = list((await session.execute(vector_stmt)).all())
    if not lexical_rows and not vector_rows:
        return []

    # 3. Reciprocal Rank Fusion (RRF)
    scores: dict[uuid.UUID, float] = {}
    hits_by_segment_id: dict[uuid.UUID, tuple[MediaSegment, MediaDocument, str]] = {}

    for rank, row in enumerate(lexical_rows, start=1):
        seg = row[0]
        doc = row[1]
        hits_by_segment_id[seg.id] = (seg, doc, "lexical")
        scores[seg.id] = scores.get(seg.id, 0.0) + 1.0 / (RRF_K + rank)

    for rank, row in enumerate(vector_rows, start=1):
        seg = row[0]
        doc = row[1]
        prev_mode = hits_by_segment_id.get(seg.id, (None, None, None))[2]
        match_mode = "hybrid" if prev_mode == "lexical" else "vector"
        hits_by_segment_id[seg.id] = (seg, doc, match_mode)
        scores[seg.id] = scores.get(seg.id, 0.0) + 1.0 / (RRF_K + rank)

    hits: list[MediaSearchHit] = []
    for seg_id, (seg, doc, mode) in hits_by_segment_id.items():
        hits.append(
            MediaSearchHit(
                document_id=doc.id,
                segment_id=seg.id,
                url=doc.url,
                media_type=doc.media_type,
                title=doc.title,
                channel_or_author=doc.channel_or_author,
                start_time_seconds=seg.start_time_seconds,
                end_time_seconds=seg.end_time_seconds,
                label=seg.label,
                content=seg.content,
                score=scores[seg_id],
                match_mode=mode,
                created_at=seg.created_at,
            )
        )

    hits.sort(key=lambda hit: (-hit.score, -hit.created_at.timestamp()))
    return hits[:limit]


async def tombstone_media_document(
    session: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Tombstone a MediaDocument by ID."""
    conditions = [
        MediaDocument.id == document_id,
        MediaDocument.tombstoned_at.is_(None),
    ]
    if user_id is not None:
        conditions.append(MediaDocument.user_id == user_id)

    stmt = (
        update(MediaDocument)
        .where(*conditions)
        .values(tombstoned_at=func.now())
    )
    result = await session.execute(stmt)
    return bool(getattr(result, "rowcount", 0) and getattr(result, "rowcount", 0) > 0)

search_media = search_media_segments


async def read_media_segment(
    session: AsyncSession,
    *,
    segment_id: uuid.UUID,
    native_user_id: str | None = None,
    user_id: uuid.UUID | None = None,
) -> tuple[MediaSegment, MediaDocument] | None:
    """Fetch active MediaSegment and its parent MediaDocument by segment ID."""
    conditions = [
        MediaSegment.id == segment_id,
        MediaDocument.tombstoned_at.is_(None),
    ]
    stmt = (
        select(MediaSegment, MediaDocument)
        .join(MediaDocument, MediaSegment.document_id == MediaDocument.id)
    )
    if user_id is not None:
        stmt = stmt.where(MediaSegment.user_id == user_id)
    elif native_user_id is not None:
        stmt = stmt.join(UserIdentity, UserIdentity.id == MediaSegment.user_id).where(
            UserIdentity.native_user_id == native_user_id
        )
    stmt = stmt.where(*conditions)
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        return None
    return row[0], row[1]


async def delete_media_document(
    session: AsyncSession,
    document_id: uuid.UUID,
) -> bool:
    """Permanently delete a MediaDocument and cascading segments."""
    stmt = sa_delete(MediaDocument).where(MediaDocument.id == document_id)
    result = await session.execute(stmt)
    return bool(getattr(result, "rowcount", 0) and getattr(result, "rowcount", 0) > 0)
