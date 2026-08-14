"""Per-user file passage store with exact-content dedup."""

import hashlib
import uuid
from datetime import UTC
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.conversation.chunking import chunk_message
from assistant_core.files.models import FileReference, FileSegment

# Reuse conversation chunking version for files until a file-specific version is needed.
FILE_CHUNKING_VERSION = "file-v1"

# Share the same overlap logic; keep file chunks bounded to 4000/400 for now.


def _file_content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def materialize_file(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    native_file_id: str,
    content: str,
) -> dict[str, int]:
    """Persist bounded file passages with per-user exact-content reuse.

    Returns counts for safe logging without content.
    """
    if not content.strip():
        return {"new_segments": 0, "reused_segments": 0, "new_references": 0}

    # Reuse the same paragraph-first chunker as conversations.
    chunks = chunk_message(content)
    if not chunks:
        return {"new_segments": 0, "reused_segments": 0, "new_references": 0}

    new_segments = 0
    reused_segments = 0
    new_references = 0

    for chunk_ordinal, chunk in enumerate(chunks):
        content_sha = _file_content_sha256(chunk)
        proposed_segment_id = uuid.uuid4()
        segment_insert = (
            insert(FileSegment)
            .values(
                id=proposed_segment_id,
                user_id=user_id,
                content_sha256=content_sha,
                chunking_version=FILE_CHUNKING_VERSION,
                content=chunk,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    FileSegment.user_id,
                    FileSegment.content_sha256,
                    FileSegment.chunking_version,
                ]
            )
            .returning(FileSegment.id)
        )
        segment_id = (await session.execute(segment_insert)).scalar_one_or_none()
        if segment_id is None:
            # Conflict — reuse existing segment
            segment_id = (
                await session.execute(
                    select(FileSegment.id).where(
                        FileSegment.user_id == user_id,
                        FileSegment.content_sha256 == content_sha,
                        FileSegment.chunking_version == FILE_CHUNKING_VERSION,
                    )
                )
            ).scalar_one()
            reused_segments += 1
        else:
            new_segments += 1

        ref_insert = (
            insert(FileReference)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                segment_id=segment_id,
                native_file_id=native_file_id,
                chunk_ordinal=chunk_ordinal,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    FileReference.user_id,
                    FileReference.native_file_id,
                    FileReference.chunk_ordinal,
                ]
            )
            .returning(FileReference.id)
        )
        if (await session.execute(ref_insert)).scalar_one_or_none() is not None:
            new_references += 1
    return {
        "new_segments": new_segments,
        "reused_segments": reused_segments,
        "new_references": new_references,
    }




async def get_file_stats(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """Return per-user file counters."""
    total_segments = (
        await session.execute(
            select(func.count()).select_from(FileSegment).where(FileSegment.user_id == user_id)
        )
    ).scalar_one()
    embedded_segments = (
        await session.execute(
            select(func.count())
            .select_from(FileSegment)
            .where(FileSegment.user_id == user_id, FileSegment.embedding.is_not(None))
        )
    ).scalar_one()
    total_references = (
        await session.execute(
            select(func.count()).select_from(FileReference).where(FileReference.user_id == user_id)
        )
    ).scalar_one()
    active_references = (
        await session.execute(
            select(func.count())
            .select_from(FileReference)
            .where(FileReference.user_id == user_id, FileReference.tombstoned_at.is_(None))
        )
    ).scalar_one()
    return {
        "total_segments": total_segments,
        "embedded_segments": embedded_segments,
        "lexical_segments": total_segments - embedded_segments,
        "total_references": total_references,
        "active_references": active_references,
        "tombstoned_references": total_references - active_references,
    }


async def tombstone_file(
    session: AsyncSession, *, user_id: uuid.UUID, native_file_id: str
) -> int:
    """Mark all active references for a file as tombstoned. Returns count."""
    from datetime import datetime

    result = await session.execute(
        select(FileReference.id).where(
            FileReference.user_id == user_id,
            FileReference.native_file_id == native_file_id,
            FileReference.tombstoned_at.is_(None),
        )
    )
    ids = [r[0] for r in result.all()]
    if not ids:
        return 0
    from sqlalchemy import update

    await session.execute(
        update(FileReference)
        .where(FileReference.id.in_(ids))
        .values(tombstoned_at=datetime.now(UTC))
    )
    return len(ids)

async def search_file_context(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    query_embedding: list[float] | None,
    limit: int,
) -> list[dict[str, object]]:
    """Hybrid file search returning bounded metadata + content for RRF.

    Returns list of dicts with keys: source_id (FileReference id), content,
    native_file_id, chunk_ordinal, score.
    """
    # Lexical candidates via FTS
    tsquery = func.plainto_tsquery("simple", query)
    lexical_stmt = (
        select(FileReference.id, FileSegment.content, FileReference.native_file_id, FileReference.chunk_ordinal)
        .join(FileSegment, FileSegment.id == FileReference.segment_id)
        .where(
            FileReference.user_id == user_id,
            FileReference.tombstoned_at.is_(None),
            FileSegment.user_id == user_id,
            FileSegment.search_vector.op("@@")(tsquery),
        )
        .order_by(func.ts_rank_cd(FileSegment.search_vector, tsquery).desc())
        .limit(40)
    )
    lexical_rows = (await session.execute(lexical_stmt)).all()

    # Vector candidates if embedding available
    vector_rows: Any = []
    if query_embedding is not None:
        try:
            vector_stmt = (
                select(
                    FileReference.id,
                    FileSegment.content,
                    FileReference.native_file_id,
                    FileReference.chunk_ordinal,
                    FileSegment.embedding.cosine_distance(query_embedding).label("dist"),
                )
                .join(FileSegment, FileSegment.id == FileReference.segment_id)
                .where(
                    FileReference.user_id == user_id,
                    FileReference.tombstoned_at.is_(None),
                    FileSegment.user_id == user_id,
                    FileSegment.embedding.is_not(None),
                )
                .order_by(text("dist"))
                .limit(40)
            )
            vector_rows = (await session.execute(vector_stmt)).all()
        except Exception:  # noqa: BLE001 - fail open to lexical
            vector_rows = []

    # RRF fusion (k=60) with deterministic tie-break on id
    scores: dict[str, float] = {}
    contents: dict[str, tuple[str, str, int]] = {}
    for rank, row in enumerate(lexical_rows, start=1):
        rid = str(row[0])
        scores[rid] = scores.get(rid, 0) + 1.0 / (60 + rank)
        contents[rid] = (row[1], row[2], row[3])
    for rank, row in enumerate(vector_rows, start=1):
        rid = str(row[0])
        scores[rid] = scores.get(rid, 0) + 1.0 / (60 + rank)
        # vector rows have content at index 1 as well
        if rid not in contents:
            contents[rid] = (row[1], row[2], row[3])

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    hits: list[dict[str, object]] = []
    for rid, score in ranked[:limit]:
        content, file_id, ordinal = contents[rid]
        hits.append(
            {
                "source_id": rid,
                "content": content,
                "native_file_id": file_id,
                "chunk_ordinal": ordinal,
                "score": score,
            }
        )
    return hits
