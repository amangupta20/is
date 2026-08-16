import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.files.chunking import CHUNKING_VERSION, chunk_markdown
from assistant_core.files.models import FileDocument, FileReference, FileSegment
from assistant_core.files.schemas import (
    FileHit,
    FileMaterializationResult,
    FilePassageContext,
    FullFileContent,
)
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job

RRF_K = 60


async def materialize_file_passages(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    native_file_id: str,
    filename: str,
    mime_type: str,
    markdown_text: str,
) -> FileMaterializationResult:
    """Chunk markdown, store full un-chunked document, and persist deduplicated segments."""
    chunks = chunk_markdown(markdown_text)
    if not chunks:
        return FileMaterializationResult(
            native_file_id=native_file_id,
            total_chunks=0,
            inserted_segments=0,
            reused_segments=0,
            missing_embedding_segment_ids=(),
        )

    # Persist or update the un-chunked full document
    doc_sha = hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()
    doc_stmt = (
        insert(FileDocument)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            native_file_id=native_file_id,
            filename=filename,
            mime_type=mime_type,
            content=markdown_text,
            content_sha256=doc_sha,
            total_chunks=len(chunks),
            total_characters=len(markdown_text),
            tombstoned_at=None,
        )
        .on_conflict_do_update(
            constraint="uq_file_document_user_file",
            set_={
                "filename": filename,
                "mime_type": mime_type,
                "content": markdown_text,
                "content_sha256": doc_sha,
                "total_chunks": len(chunks),
                "total_characters": len(markdown_text),
                "tombstoned_at": None,
            },
        )
    )
    await session.execute(doc_stmt)

    missing_embedding_ids: list[str] = []
    inserted_segments = 0
    reused_segments = 0

    for chunk in chunks:
        # Upsert file segment based on (user_id, content_sha256, chunking_version)
        stmt = (
            insert(FileSegment)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                content_sha256=chunk.content_sha256,
                chunking_version=CHUNKING_VERSION,
                content=chunk.text,
            )
            .on_conflict_do_nothing(constraint="uq_file_segment_user_hash_version")
            .returning(FileSegment.id)
        )
        res = await session.execute(stmt)
        inserted_id = res.scalar_one_or_none()

        if inserted_id is not None:
            segment_id = inserted_id
            inserted_segments += 1
            missing_embedding_ids.append(str(segment_id))
        else:
            # Reused existing segment
            existing_stmt = select(
                FileSegment.id,
                FileSegment.embedding.is_(None),
            ).where(
                FileSegment.user_id == user_id,
                FileSegment.content_sha256 == chunk.content_sha256,
                FileSegment.chunking_version == CHUNKING_VERSION,
            )
            row = (await session.execute(existing_stmt)).scalar_one_or_none()
            if row is not None:
                if isinstance(row, tuple):
                    segment_id, embedding_missing = row
                else:
                    segment_id = row
                    embedding_missing = False
                reused_segments += 1
                if embedding_missing:
                    missing_embedding_ids.append(str(segment_id))
            else:
                segment_id = uuid.uuid4()

        # Insert file reference
        ref_stmt = (
            insert(FileReference)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                segment_id=segment_id,
                native_file_id=native_file_id,
                filename=filename,
                mime_type=mime_type,
                header_path=chunk.header_path,
                chunk_ordinal=chunk.chunk_ordinal,
            )
            .on_conflict_do_nothing(constraint="uq_file_reference_user_file_chunk")
        )
        await session.execute(ref_stmt)

    return FileMaterializationResult(
        native_file_id=native_file_id,
        total_chunks=len(chunks),
        inserted_segments=inserted_segments,
        reused_segments=reused_segments,
        missing_embedding_segment_ids=tuple(missing_embedding_ids),
    )


async def tombstone_file_references(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    native_file_id: str,
) -> int:
    """Tombstone all active references and document for a deleted native file."""
    now = datetime.now(UTC)
    doc_stmt = (
        update(FileDocument)
        .where(
            FileDocument.user_id == user_id,
            FileDocument.native_file_id == native_file_id,
            FileDocument.tombstoned_at.is_(None),
        )
        .values(tombstoned_at=now)
    )
    await session.execute(doc_stmt)

    stmt = (
        update(FileReference)
        .where(
            FileReference.user_id == user_id,
            FileReference.native_file_id == native_file_id,
            FileReference.tombstoned_at.is_(None),
        )
        .values(tombstoned_at=now)
        .returning(FileReference.id)
    )
    result = await session.execute(stmt)
    return len(result.scalars().all())


async def get_file_segment_content(session: AsyncSession, segment_id: uuid.UUID) -> str | None:
    """Load exact text for a segment missing an embedding."""
    stmt = select(FileSegment.content).where(
        FileSegment.id == segment_id,
        FileSegment.embedding.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def store_file_segment_embedding(
    session: AsyncSession,
    segment_id: uuid.UUID,
    embedding: list[float],
    *,
    model: str,
    dimension: int,
    version: str,
) -> None:
    """Persist an embedding vector on an existing file segment."""
    stmt = (
        update(FileSegment)
        .where(FileSegment.id == segment_id)
        .values(
            embedding=embedding,
            embedding_model=model,
            embedding_dimension=dimension,
            embedding_version=version,
            embedded_at=datetime.now(UTC),
        )
    )
    await session.execute(stmt)


async def search_file_passages(
    session: AsyncSession,
    *,
    native_user_id: str | None = None,
    user_id: uuid.UUID | None = None,
    query_text: str,
    query_embedding: list[float] | None = None,
    limit: int = 10,
) -> list[FileHit]:
    """Retrieve top file passages using hybrid FTS + pgvector cosine similarity."""
    lexical_hits: dict[uuid.UUID, FileHit] = {}

    user_filters = []
    if user_id is not None:
        user_filters.append(FileReference.user_id == user_id)
    elif native_user_id is not None:
        user_filters.append(UserIdentity.native_user_id == native_user_id)

    # Lexical search via TSVECTOR
    if query_text.strip():
        lexical_stmt = select(
            FileReference.id,
            FileReference.segment_id,
            FileReference.native_file_id,
            FileReference.filename,
            FileReference.header_path,
            FileReference.chunk_ordinal,
            FileSegment.content,
        ).join(FileSegment, FileReference.segment_id == FileSegment.id)
        if native_user_id is not None and user_id is None:
            lexical_stmt = lexical_stmt.join(UserIdentity, UserIdentity.id == FileReference.user_id)
        lexical_stmt = lexical_stmt.where(
            *user_filters,
            FileReference.tombstoned_at.is_(None),
            FileSegment.search_vector.op("@@")(func.plainto_tsquery("simple", query_text)),
        ).limit(limit)

        lexical_rows = (await session.execute(lexical_stmt)).all()
        for rank, row in enumerate(lexical_rows, start=1):
            ref_id, seg_id, file_id, fname, hpath, ordinal, content = row
            lexical_hits[ref_id] = FileHit(
                reference_id=str(ref_id),
                segment_id=str(seg_id),
                native_file_id=file_id,
                filename=fname,
                header_path=hpath,
                chunk_ordinal=ordinal,
                content=content,
                lexical_rank=rank,
            )

    semantic_hits: dict[uuid.UUID, FileHit] = {}
    if query_embedding is not None:
        semantic_stmt = select(
            FileReference.id,
            FileReference.segment_id,
            FileReference.native_file_id,
            FileReference.filename,
            FileReference.header_path,
            FileReference.chunk_ordinal,
            FileSegment.content,
        ).join(FileSegment, FileReference.segment_id == FileSegment.id)
        if native_user_id is not None and user_id is None:
            semantic_stmt = semantic_stmt.join(
                UserIdentity, UserIdentity.id == FileReference.user_id
            )
        semantic_stmt = (
            semantic_stmt.where(
                *user_filters,
                FileReference.tombstoned_at.is_(None),
                FileSegment.embedding.is_not(None),
            )
            .order_by(FileSegment.embedding.cosine_distance(query_embedding))
            .limit(limit)
        )
        semantic_rows = (await session.execute(semantic_stmt)).all()
        for rank, row in enumerate(semantic_rows, start=1):
            ref_id, seg_id, file_id, fname, hpath, ordinal, content = row
            semantic_hits[ref_id] = FileHit(
                reference_id=str(ref_id),
                segment_id=str(seg_id),
                native_file_id=file_id,
                filename=fname,
                header_path=hpath,
                chunk_ordinal=ordinal,
                content=content,
                semantic_rank=rank,
            )

    # Merge via Reciprocal Rank Fusion (RRF)
    all_ref_ids = set(lexical_hits.keys()) | set(semantic_hits.keys())
    fused_hits: list[FileHit] = []

    for ref_id in all_ref_ids:
        lex = lexical_hits.get(ref_id)
        sem = semantic_hits.get(ref_id)
        base = lex or sem
        if not base:
            continue

        score = 0.0
        lex_rank = lex.lexical_rank if lex else None
        sem_rank = sem.semantic_rank if sem else None

        if lex_rank is not None:
            score += 1.0 / (RRF_K + lex_rank)
        if sem_rank is not None:
            score += 1.0 / (RRF_K + sem_rank)

        fused_hits.append(
            FileHit(
                reference_id=base.reference_id,
                segment_id=base.segment_id,
                native_file_id=base.native_file_id,
                filename=base.filename,
                header_path=base.header_path,
                chunk_ordinal=base.chunk_ordinal,
                content=base.content,
                lexical_rank=lex_rank,
                semantic_rank=sem_rank,
                score=score,
            )
        )

    fused_hits.sort(key=lambda h: h.score, reverse=True)
    return fused_hits[:limit]


async def read_file_passage_context(
    session: AsyncSession,
    *,
    reference_id: uuid.UUID,
    native_user_id: str | None = None,
    user_id: uuid.UUID | None = None,
) -> FilePassageContext | None:
    """Read a specific file reference and its adjacent ordinal neighbors."""
    user_filters = []
    if user_id is not None:
        user_filters.append(FileReference.user_id == user_id)
    elif native_user_id is not None:
        user_filters.append(UserIdentity.native_user_id == native_user_id)

    target_stmt = select(
        FileReference.id,
        FileReference.user_id,
        FileReference.native_file_id,
        FileReference.filename,
        FileReference.mime_type,
        FileReference.header_path,
        FileReference.chunk_ordinal,
        FileSegment.content,
    ).join(FileSegment, FileReference.segment_id == FileSegment.id)
    if native_user_id is not None and user_id is None:
        target_stmt = target_stmt.join(UserIdentity, UserIdentity.id == FileReference.user_id)
    target_stmt = target_stmt.where(
        *user_filters,
        FileReference.id == reference_id,
        FileReference.tombstoned_at.is_(None),
    )
    row = (await session.execute(target_stmt)).one_or_none()
    if not row:
        return None

    ref_id, owner_id, native_file_id, filename, mime_type, header_path, ordinal, content = row

    # Fetch previous chunk
    prev_stmt = (
        select(FileSegment.content)
        .join(FileReference, FileReference.segment_id == FileSegment.id)
        .where(
            FileReference.user_id == owner_id,
            FileReference.native_file_id == native_file_id,
            FileReference.chunk_ordinal == ordinal - 1,
            FileReference.tombstoned_at.is_(None),
        )
    )
    prev_content = (await session.execute(prev_stmt)).scalar_one_or_none()

    # Fetch next chunk
    next_stmt = (
        select(FileSegment.content)
        .join(FileReference, FileReference.segment_id == FileSegment.id)
        .where(
            FileReference.user_id == owner_id,
            FileReference.native_file_id == native_file_id,
            FileReference.chunk_ordinal == ordinal + 1,
            FileReference.tombstoned_at.is_(None),
        )
    )
    next_content = (await session.execute(next_stmt)).scalar_one_or_none()

    return FilePassageContext(
        reference_id=str(ref_id),
        native_file_id=native_file_id,
        filename=filename,
        mime_type=mime_type,
        header_path=header_path,
        chunk_ordinal=ordinal,
        content=content,
        previous_content=prev_content,
        next_content=next_content,
    )


async def get_full_file_content(
    session: AsyncSession,
    *,
    file_id_or_name: str,
    native_user_id: str | None = None,
    user_id: uuid.UUID | None = None,
) -> FullFileContent | None:
    """Retrieve the full un-chunked document content directly from FileDocument."""
    user_filters = []
    if user_id is not None:
        user_filters.append(FileDocument.user_id == user_id)
    elif native_user_id is not None:
        user_filters.append(UserIdentity.native_user_id == native_user_id)

    stmt = select(
        FileDocument.native_file_id,
        FileDocument.filename,
        FileDocument.mime_type,
        FileDocument.total_chunks,
        FileDocument.total_characters,
        FileDocument.content,
    )
    if native_user_id is not None and user_id is None:
        stmt = stmt.join(UserIdentity, UserIdentity.id == FileDocument.user_id)
    stmt = stmt.where(
        *user_filters,
        FileDocument.tombstoned_at.is_(None),
        (FileDocument.native_file_id == file_id_or_name)
        | (FileDocument.filename == file_id_or_name),
    )
    row = (await session.execute(stmt)).first()
    if not row:
        return None

    return FullFileContent(
        native_file_id=row[0],
        filename=row[1],
        mime_type=row[2],
        total_chunks=row[3],
        total_characters=row[4],
        content=row[5],
    )


async def get_file_stats(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """Return aggregated file index and queue stats for an owner."""
    total_files = (
        await session.execute(
            select(func.count(func.distinct(FileReference.native_file_id))).where(
                FileReference.user_id == user_id
            )
        )
    ).scalar_one()

    active_files = (
        await session.execute(
            select(func.count(func.distinct(FileReference.native_file_id))).where(
                FileReference.user_id == user_id,
                FileReference.tombstoned_at.is_(None),
            )
        )
    ).scalar_one()

    tombstoned_files = (
        await session.execute(
            select(func.count(func.distinct(FileReference.native_file_id))).where(
                FileReference.user_id == user_id,
                FileReference.tombstoned_at.is_not(None),
            )
        )
    ).scalar_one()

    total_segments = (
        await session.execute(
            select(func.count(FileSegment.id)).where(FileSegment.user_id == user_id)
        )
    ).scalar_one()

    embedded_segments = (
        await session.execute(
            select(func.count(FileSegment.id)).where(
                FileSegment.user_id == user_id,
                FileSegment.embedding.is_not(None),
            )
        )
    ).scalar_one()

    total_references = (
        await session.execute(
            select(func.count(FileReference.id)).where(FileReference.user_id == user_id)
        )
    ).scalar_one()

    last_indexed_at = (
        await session.execute(
            select(func.max(FileReference.created_at)).where(FileReference.user_id == user_id)
        )
    ).scalar_one_or_none()

    queued_jobs = (
        await session.execute(select(func.count()).select_from(Job).where(Job.status == "queued"))
    ).scalar_one()

    dead_jobs = (
        await session.execute(select(func.count()).select_from(Job).where(Job.status == "dead"))
    ).scalar_one()

    reused_segments = max(0, total_references - total_segments)

    return {
        "total_files": total_files,
        "active_files": active_files,
        "tombstoned_files": tombstoned_files,
        "total_segments": total_segments,
        "embedded_segments": embedded_segments,
        "lexical_segments": total_segments - embedded_segments,
        "reused_segments": reused_segments,
        "queued_jobs": queued_jobs,
        "dead_jobs": dead_jobs,
        "last_indexed_at": last_indexed_at,
    }


async def get_recent_files(
    session: AsyncSession, user_id: uuid.UUID, limit: int = 10
) -> list[dict[str, Any]]:
    """Return recently indexed files with their metadata and chunk counts."""
    stmt = (
        select(
            FileReference.native_file_id,
            FileReference.filename,
            FileReference.mime_type,
            func.min(FileReference.created_at).label("created_at"),
            func.max(FileReference.tombstoned_at).label("tombstoned_at"),
            func.count(FileReference.id).label("chunk_count"),
        )
        .where(FileReference.user_id == user_id)
        .group_by(
            FileReference.native_file_id,
            FileReference.filename,
            FileReference.mime_type,
        )
        .order_by(func.min(FileReference.created_at).desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    results = []
    for r in rows:
        fid, fname, mime, created, tombstoned, count = r
        status = "tombstoned" if tombstoned is not None else "indexed"
        results.append(
            {
                "native_file_id": fid,
                "filename": fname,
                "mime_type": mime,
                "chunk_count": count,
                "created_at": created,
                "tombstoned_at": tombstoned,
                "status": status,
            }
        )
    return results


async def get_dead_jobs(session: AsyncSession, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent dead jobs with failure details."""
    stmt = (
        select(
            Job.id,
            Job.kind,
            Job.identity_key,
            Job.attempts,
            Job.last_error_code,
            Job.available_at,
            Job.claimed_at,
        )
        .where(Job.status == "dead")
        .order_by(Job.available_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "job_id": str(r[0]),
            "kind": r[1],
            "identity_key": r[2],
            "attempts": r[3],
            "last_error": r[4],
            "available_at": r[5],
            "claimed_at": r[6],
        }
        for r in rows
    ]
