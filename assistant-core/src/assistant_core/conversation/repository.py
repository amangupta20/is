"""Idempotent lexical passage materialization for completed turns."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.conversation.chunking import CHUNKING_VERSION, build_turn_passages
from assistant_core.conversation.models import ConversationReference, ConversationSegment
from assistant_core.conversation.schemas import (
    ConversationHit,
    ConversationNeighbor,
    ConversationRead,
    ConversationRole,
    PassageMaterialization,
    TombstoneResult,
)
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.turns.models import CompletedTurn

CONVERSATION_HASH_COLLISION_ERROR = "conversation_hash_collision"
RRF_K = 60
SEARCH_CANDIDATE_LIMIT = 40
MAX_READ_CHARS = 12_000


class ConversationHashCollisionError(RuntimeError):
    """Raised without source text when an exact hash maps to different content."""


async def enqueue_missing_conversation_jobs(session: AsyncSession) -> int:
    """Idempotently queue indexing for every active completed turn."""
    turn_ids = (
        await session.execute(
            select(CompletedTurn.id).where(CompletedTurn.tombstoned_at.is_(None))
        )
    ).scalars()
    enqueued = 0
    for turn_id in turn_ids:
        result = await session.execute(
            insert(Job)
            .values(
                id=uuid.uuid4(),
                identity_key=f"conversation:{turn_id}:{CHUNKING_VERSION}",
                kind="index_conversation",
                status="queued",
                payload={"turn_id": str(turn_id)},
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=[Job.identity_key])
            .returning(Job.id)
        )
        if result.scalar_one_or_none() is not None:
            enqueued += 1
    return enqueued


async def get_segment_content(
    session: AsyncSession, segment_id: uuid.UUID
) -> str | None:
    """Load exact text only while a segment still needs an embedding."""
    return (
        await session.execute(
            select(ConversationSegment.content).where(
                ConversationSegment.id == segment_id,
                ConversationSegment.embedding.is_(None),
            )
        )
    ).scalar_one_or_none()


async def store_segment_embedding(
    session: AsyncSession,
    segment_id: uuid.UUID,
    embedding: list[float],
    *,
    model: str,
    dimension: int,
    version: str,
    embedded_at: datetime,
) -> bool:
    """Store one vector only when another worker has not already done so."""
    result = await session.execute(
        update(ConversationSegment)
        .where(
            ConversationSegment.id == segment_id,
            ConversationSegment.embedding.is_(None),
        )
        .values(
            embedding=embedding,
            embedding_model=model,
            embedding_dimension=dimension,
            embedding_version=version,
            embedded_at=embedded_at,
        )
    )
    return bool(getattr(result, "rowcount", 0))


def _conversation_hit(
    reference: ConversationReference,
    segment: ConversationSegment,
    score: float,
) -> ConversationHit:
    """Convert one trusted database row into a retrieval value."""
    role: ConversationRole = "user" if reference.role == "user" else "assistant"
    return ConversationHit(
        source_id=reference.id,
        content=segment.content,
        role=role,
        native_chat_id=reference.native_chat_id,
        native_message_id=reference.native_message_id,
        occurred_at=reference.occurred_at,
        score=score,
    )


async def search_conversation_context(
    session: AsyncSession,
    *,
    native_user_id: str,
    query: str,
    query_embedding: list[float] | None,
    limit: int,
) -> list[ConversationHit]:
    """Fuse owner-scoped PostgreSQL lexical and vector reference candidates."""
    candidate_limit = min(max(limit * 4, limit), SEARCH_CANDIDATE_LIMIT)
    tsquery = func.websearch_to_tsquery("simple", query)
    lexical_rank = func.ts_rank_cd(
        ConversationSegment.search_vector, tsquery
    ).label("lexical_rank")
    active_filters = (
        UserIdentity.native_user_id == native_user_id,
        ConversationSegment.user_id == ConversationReference.user_id,
        CompletedTurn.user_id == ConversationReference.user_id,
        ConversationReference.tombstoned_at.is_(None),
        CompletedTurn.tombstoned_at.is_(None),
    )
    lexical_rows = (
        await session.execute(
            select(ConversationReference, ConversationSegment, lexical_rank)
            .join(
                ConversationSegment,
                ConversationSegment.id == ConversationReference.segment_id,
            )
            .join(CompletedTurn, CompletedTurn.id == ConversationReference.completed_turn_id)
            .join(UserIdentity, UserIdentity.id == ConversationReference.user_id)
            .where(
                *active_filters,
                ConversationSegment.search_vector.op("@@")(tsquery),
            )
            .order_by(
                lexical_rank.desc(),
                ConversationReference.occurred_at.desc(),
                ConversationReference.id.asc(),
            )
            .limit(candidate_limit)
        )
    ).all()

    vector_rows: list[Any] = []
    if query_embedding is not None:
        distance = ConversationSegment.embedding.cosine_distance(query_embedding).label(
            "vector_distance"
        )
        vector_rows = list(
            (
                await session.execute(
                    select(ConversationReference, ConversationSegment, distance)
                    .join(
                        ConversationSegment,
                        ConversationSegment.id == ConversationReference.segment_id,
                    )
                    .join(
                        CompletedTurn,
                        CompletedTurn.id == ConversationReference.completed_turn_id,
                    )
                    .join(UserIdentity, UserIdentity.id == ConversationReference.user_id)
                    .where(
                        *active_filters,
                        ConversationSegment.embedding.is_not(None),
                    )
                    .order_by(
                        distance.asc(),
                        ConversationReference.occurred_at.desc(),
                        ConversationReference.id.asc(),
                    )
                    .limit(candidate_limit)
                )
            ).all()
        )

    scores: dict[uuid.UUID, float] = {}
    rows_by_id: dict[uuid.UUID, tuple[ConversationReference, ConversationSegment]] = {}
    for ranked_rows in (lexical_rows, vector_rows):
        for rank, row in enumerate(ranked_rows, start=1):
            reference, segment = row[0], row[1]
            rows_by_id[reference.id] = (reference, segment)
            scores[reference.id] = scores.get(reference.id, 0.0) + 1.0 / (RRF_K + rank)

    hits = [
        _conversation_hit(reference, segment, scores[source_id])
        for source_id, (reference, segment) in rows_by_id.items()
    ]
    hits.sort(
        key=lambda hit: (-hit.score, -hit.occurred_at.timestamp(), str(hit.source_id))
    )
    return hits[:limit]


def _neighbor(
    row: Any | None,
    remaining_chars: int,
) -> ConversationNeighbor | None:
    if row is None or remaining_chars <= 0:
        return None
    reference, segment = row
    role: ConversationRole = "user" if reference.role == "user" else "assistant"
    return ConversationNeighbor(
        role=role,
        content=segment.content[:remaining_chars],
        native_chat_id=reference.native_chat_id,
        native_message_id=reference.native_message_id,
    )


async def read_conversation_context(
    session: AsyncSession,
    *,
    native_user_id: str,
    source_id: uuid.UUID,
) -> ConversationRead | None:
    """Resolve one active source plus immediate active neighbors within 12k chars."""
    selected_row = (
        await session.execute(
            select(ConversationReference, ConversationSegment)
            .join(
                ConversationSegment,
                ConversationSegment.id == ConversationReference.segment_id,
            )
            .join(CompletedTurn, CompletedTurn.id == ConversationReference.completed_turn_id)
            .join(UserIdentity, UserIdentity.id == ConversationReference.user_id)
            .where(
                UserIdentity.native_user_id == native_user_id,
                ConversationReference.id == source_id,
                ConversationSegment.user_id == ConversationReference.user_id,
                CompletedTurn.user_id == ConversationReference.user_id,
                ConversationReference.tombstoned_at.is_(None),
                CompletedTurn.tombstoned_at.is_(None),
            )
        )
    ).one_or_none()
    if selected_row is None:
        return None
    selected_reference, selected_segment = selected_row[0], selected_row[1]
    position = (
        selected_reference.occurred_at,
        selected_reference.role_order,
        selected_reference.chunk_ordinal,
        selected_reference.id,
    )
    ordering = tuple_(
        ConversationReference.occurred_at,
        ConversationReference.role_order,
        ConversationReference.chunk_ordinal,
        ConversationReference.id,
    )
    neighbor_base = (
        select(ConversationReference, ConversationSegment)
        .join(
            ConversationSegment,
            ConversationSegment.id == ConversationReference.segment_id,
        )
        .join(CompletedTurn, CompletedTurn.id == ConversationReference.completed_turn_id)
        .where(
            ConversationReference.user_id == selected_reference.user_id,
            ConversationReference.native_chat_id == selected_reference.native_chat_id,
            ConversationSegment.user_id == ConversationReference.user_id,
            CompletedTurn.user_id == ConversationReference.user_id,
            ConversationReference.tombstoned_at.is_(None),
            CompletedTurn.tombstoned_at.is_(None),
        )
    )
    previous_row = (
        await session.execute(
            neighbor_base.where(ordering < position)
            .order_by(
                ConversationReference.occurred_at.desc(),
                ConversationReference.role_order.desc(),
                ConversationReference.chunk_ordinal.desc(),
                ConversationReference.id.desc(),
            )
            .limit(1)
        )
    ).one_or_none()
    next_row = (
        await session.execute(
            neighbor_base.where(ordering > position)
            .order_by(
                ConversationReference.occurred_at.asc(),
                ConversationReference.role_order.asc(),
                ConversationReference.chunk_ordinal.asc(),
                ConversationReference.id.asc(),
            )
            .limit(1)
        )
    ).one_or_none()

    selected = _conversation_hit(selected_reference, selected_segment, 1.0)
    remaining_chars = max(MAX_READ_CHARS - len(selected.content), 0)
    neighbors: list[ConversationNeighbor] = []
    previous = _neighbor(previous_row, remaining_chars)
    if previous is not None:
        neighbors.append(previous)
        remaining_chars -= len(previous.content)
    following = _neighbor(next_row, remaining_chars)
    if following is not None:
        neighbors.append(following)
    return ConversationRead(selected=selected, neighbors=tuple(neighbors))


async def tombstone_chat(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    native_chat_id: str,
    occurred_at: datetime,
) -> TombstoneResult:
    """Hide one user's native chat while retaining reusable canonical segments."""
    affected_segment_ids = list(
        (
            await session.execute(
                select(ConversationReference.segment_id)
                .where(
                    ConversationReference.user_id == user_id,
                    ConversationReference.native_chat_id == native_chat_id,
                    ConversationReference.tombstoned_at.is_(None),
                )
                .distinct()
            )
        ).scalars().all()
    )
    reference_ids = (
        await session.execute(
            update(ConversationReference)
            .where(
                ConversationReference.user_id == user_id,
                ConversationReference.native_chat_id == native_chat_id,
                ConversationReference.tombstoned_at.is_(None),
            )
            .values(tombstoned_at=occurred_at)
            .returning(ConversationReference.id)
        )
    ).scalars().all()
    turn_ids = (
        await session.execute(
            update(CompletedTurn)
            .where(
                CompletedTurn.user_id == user_id,
                CompletedTurn.native_chat_id == native_chat_id,
                CompletedTurn.tombstoned_at.is_(None),
            )
            .values(tombstoned_at=occurred_at)
            .returning(CompletedTurn.id)
        )
    ).scalars().all()
    orphan_segment_ids = (
        await session.execute(
            select(ConversationSegment.id).where(
                ConversationSegment.id.in_(affected_segment_ids),
                ~exists(
                    select(ConversationReference.id).where(
                        ConversationReference.segment_id == ConversationSegment.id,
                        ConversationReference.tombstoned_at.is_(None),
                    )
                ),
            )
        )
    ).scalars().all()
    return TombstoneResult(
        reference_count=len(reference_ids),
        turn_count=len(turn_ids),
        orphan_segment_count=len(orphan_segment_ids),
    )


async def materialize_turn_passages(
    session: AsyncSession, turn: CompletedTurn
) -> PassageMaterialization:
    """Persist bounded lexical passages and independent references idempotently."""
    new_segments = 0
    reused_segments = 0
    new_references = 0
    missing_embedding_ids: list[uuid.UUID] = []

    for passage in build_turn_passages(turn):
        proposed_segment_id = uuid.uuid4()
        segment_insert = (
            insert(ConversationSegment)
            .values(
                id=proposed_segment_id,
                user_id=turn.user_id,
                content_sha256=passage.content_sha256,
                chunking_version=CHUNKING_VERSION,
                content=passage.content,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ConversationSegment.user_id,
                    ConversationSegment.content_sha256,
                    ConversationSegment.chunking_version,
                ]
            )
            .returning(ConversationSegment.id)
        )
        segment_id = (await session.execute(segment_insert)).scalar_one_or_none()
        if segment_id is None:
            segment_id, stored_content, embedding = (
                await session.execute(
                    select(
                        ConversationSegment.id,
                        ConversationSegment.content,
                        ConversationSegment.embedding,
                    ).where(
                        ConversationSegment.user_id == turn.user_id,
                        ConversationSegment.content_sha256 == passage.content_sha256,
                        ConversationSegment.chunking_version == CHUNKING_VERSION,
                    )
                )
            ).one()
            if stored_content != passage.content:
                raise ConversationHashCollisionError(CONVERSATION_HASH_COLLISION_ERROR)
            reused_segments += 1
            if embedding is None and segment_id not in missing_embedding_ids:
                missing_embedding_ids.append(segment_id)
        else:
            new_segments += 1
            missing_embedding_ids.append(segment_id)

        reference_insert = (
            insert(ConversationReference)
            .values(
                id=uuid.uuid4(),
                user_id=turn.user_id,
                completed_turn_id=turn.id,
                segment_id=segment_id,
                native_chat_id=turn.native_chat_id,
                native_message_id=passage.native_message_id,
                role=passage.role,
                role_order=passage.role_order,
                chunk_ordinal=passage.chunk_ordinal,
                occurred_at=turn.occurred_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ConversationReference.user_id,
                    ConversationReference.native_chat_id,
                    ConversationReference.native_message_id,
                    ConversationReference.role,
                    ConversationReference.chunk_ordinal,
                ]
            )
            .returning(ConversationReference.id)
        )
        if (await session.execute(reference_insert)).scalar_one_or_none() is not None:
            new_references += 1

    return PassageMaterialization(
        new_segments=new_segments,
        reused_segments=reused_segments,
        new_references=new_references,
        missing_embedding_ids=tuple(missing_embedding_ids),
    )
