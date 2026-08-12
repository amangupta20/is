"""Idempotent lexical passage materialization for completed turns."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.conversation.chunking import CHUNKING_VERSION, build_turn_passages
from assistant_core.conversation.models import ConversationReference, ConversationSegment
from assistant_core.conversation.schemas import PassageMaterialization
from assistant_core.jobs.models import Job
from assistant_core.turns.models import CompletedTurn

CONVERSATION_HASH_COLLISION_ERROR = "conversation_hash_collision"


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
