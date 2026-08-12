"""Per-user file passage store with exact-content dedup."""

import hashlib
import uuid

from sqlalchemy import func, select
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
