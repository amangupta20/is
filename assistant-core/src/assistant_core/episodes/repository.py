"""Repository for TopicEpisode CRUD, hybrid search, and inactivity detection."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.episodes.models import TopicEpisode
from assistant_core.episodes.schemas import (
    TopicEpisodeDetail,
    TopicEpisodeExtraction,
    TopicEpisodeHit,
)
from assistant_core.identity.models import UserIdentity
from assistant_core.turns.models import CompletedTurn

RRF_K = 60
EPISODE_CANDIDATE_LIMIT = 20


async def create_topic_episode(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    native_chat_id: str,
    native_project_id: str | None = None,
    native_folder_id: str | None = None,
    extraction: TopicEpisodeExtraction,
    turn_count: int,
    embedding: list[float] | None = None,
) -> TopicEpisode:
    """Create and persist a new TopicEpisode."""
    episode = TopicEpisode(
        id=uuid.uuid4(),
        user_id=user_id,
        native_chat_id=native_chat_id,
        native_project_id=native_project_id,
        native_folder_id=native_folder_id,
        title=extraction.title,
        topic_category=extraction.topic_category or "general",
        summary=extraction.summary,
        decisions_made=extraction.decisions_made,
        open_loops=extraction.open_loops,
        key_entities=extraction.key_entities,
        start_message_id=extraction.start_message_id,
        end_message_id=extraction.end_message_id,
        turn_count=turn_count,
        embedding=embedding,
    )
    session.add(episode)
    await session.flush()
    return episode


async def get_topic_episode(
    session: AsyncSession,
    *,
    episode_id: uuid.UUID,
    native_user_id: str | None = None,
) -> TopicEpisodeDetail | None:
    """Fetch an active topic episode by ID."""
    stmt = (
        select(TopicEpisode)
        .join(UserIdentity, UserIdentity.id == TopicEpisode.user_id)
        .where(
            TopicEpisode.id == episode_id,
            TopicEpisode.tombstoned_at.is_(None),
        )
    )
    if native_user_id:
        stmt = stmt.where(UserIdentity.native_user_id == native_user_id)

    result = await session.execute(stmt)
    ep = result.scalar_one_or_none()
    if ep is None:
        return None

    return TopicEpisodeDetail(
        id=ep.id,
        user_id=ep.user_id,
        native_chat_id=ep.native_chat_id,
        native_project_id=ep.native_project_id,
        native_folder_id=ep.native_folder_id,
        title=ep.title,
        topic_category=ep.topic_category,
        summary=ep.summary,
        decisions_made=ep.decisions_made or [],
        open_loops=ep.open_loops or [],
        key_entities=ep.key_entities or [],
        start_message_id=ep.start_message_id,
        end_message_id=ep.end_message_id,
        turn_count=ep.turn_count,
        has_embedding=ep.embedding is not None,
        tombstoned=ep.tombstoned_at is not None,
        created_at=ep.created_at,
        updated_at=ep.updated_at,
    )


async def list_topic_episodes(
    session: AsyncSession,
    *,
    native_user_id: str | None = None,
    native_chat_id: str | None = None,
    include_tombstoned: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[TopicEpisodeDetail]:
    """List topic episodes with filtering and paging."""
    stmt = (
        select(TopicEpisode)
        .join(UserIdentity, UserIdentity.id == TopicEpisode.user_id)
        .order_by(TopicEpisode.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if native_user_id:
        stmt = stmt.where(UserIdentity.native_user_id == native_user_id)
    if native_chat_id:
        stmt = stmt.where(TopicEpisode.native_chat_id == native_chat_id)
    if not include_tombstoned:
        stmt = stmt.where(TopicEpisode.tombstoned_at.is_(None))

    result = await session.execute(stmt)
    episodes = result.scalars().all()

    return [
        TopicEpisodeDetail(
            id=ep.id,
            user_id=ep.user_id,
            native_chat_id=ep.native_chat_id,
            native_project_id=ep.native_project_id,
            native_folder_id=ep.native_folder_id,
            title=ep.title,
            topic_category=ep.topic_category,
            summary=ep.summary,
            decisions_made=ep.decisions_made or [],
            open_loops=ep.open_loops or [],
            key_entities=ep.key_entities or [],
            start_message_id=ep.start_message_id,
            end_message_id=ep.end_message_id,
            turn_count=ep.turn_count,
            has_embedding=ep.embedding is not None,
            tombstoned=ep.tombstoned_at is not None,
            created_at=ep.created_at,
            updated_at=ep.updated_at,
        )
        for ep in episodes
    ]


async def search_topic_episodes(
    session: AsyncSession,
    *,
    native_user_id: str,
    query_text: str,
    query_embedding: list[float] | None = None,
    limit: int = 5,
    native_project_id: str | None = None,
    native_folder_id: str | None = None,
) -> list[TopicEpisodeHit]:
    """Search topic episodes via hybrid FTS and vector cosine similarity with RRF."""
    # Resolve internal user_id
    user_row = (
        await session.execute(
            select(UserIdentity.id).where(UserIdentity.native_user_id == native_user_id)
        )
    ).scalar_one_or_none()
    if user_row is None:
        return []
    user_id = user_row

    # 1. Lexical candidate query
    lexical_stmt = (
        select(TopicEpisode)
        .where(
            TopicEpisode.user_id == user_id,
            TopicEpisode.tombstoned_at.is_(None),
            TopicEpisode.search_vector.op("@@")(func.plainto_tsquery("english", query_text)),
        )
        .order_by(
            func.ts_rank_cd(
                TopicEpisode.search_vector, func.plainto_tsquery("english", query_text)
            ).desc(),
            TopicEpisode.created_at.desc(),
        )
        .limit(EPISODE_CANDIDATE_LIMIT)
    )
    lexical_rows = (await session.execute(lexical_stmt)).scalars().all()

    # 2. Vector candidate query
    vector_rows: list[TopicEpisode] = []
    if query_embedding is not None and len(query_embedding) == 1536:
        vector_stmt = (
            select(TopicEpisode)
            .where(
                TopicEpisode.user_id == user_id,
                TopicEpisode.tombstoned_at.is_(None),
                TopicEpisode.embedding.is_not(None),
            )
            .order_by(
                TopicEpisode.embedding.cosine_distance(query_embedding),
                TopicEpisode.created_at.desc(),
            )
            .limit(EPISODE_CANDIDATE_LIMIT)
        )
        vector_rows = list((await session.execute(vector_stmt)).scalars().all())

    # If both empty, fallback to recent active episodes if query is non-empty
    if not lexical_rows and not vector_rows:
        return []

    # 3. Reciprocal Rank Fusion (RRF)
    scores: dict[uuid.UUID, float] = {}
    episodes_by_id: dict[uuid.UUID, TopicEpisode] = {}

    for rank, ep in enumerate(lexical_rows, start=1):
        episodes_by_id[ep.id] = ep
        scores[ep.id] = scores.get(ep.id, 0.0) + 1.0 / (RRF_K + rank)

    for rank, ep in enumerate(vector_rows, start=1):
        episodes_by_id[ep.id] = ep
        scores[ep.id] = scores.get(ep.id, 0.0) + 1.0 / (RRF_K + rank)

    # Apply project and folder scoping boosts
    for ep_id, ep in episodes_by_id.items():
        boost = 0.0
        if native_project_id and ep.native_project_id == native_project_id:
            boost += 0.05
        if native_folder_id and ep.native_folder_id == native_folder_id:
            boost += 0.03
        scores[ep_id] += boost

    hits = [
        TopicEpisodeHit(
            episode_id=ep.id,
            native_chat_id=ep.native_chat_id,
            native_project_id=ep.native_project_id,
            native_folder_id=ep.native_folder_id,
            title=ep.title,
            topic_category=ep.topic_category,
            summary=ep.summary,
            decisions_made=ep.decisions_made or [],
            open_loops=ep.open_loops or [],
            key_entities=ep.key_entities or [],
            start_message_id=ep.start_message_id,
            end_message_id=ep.end_message_id,
            turn_count=ep.turn_count,
            score=scores[ep.id],
            match_mode="hybrid" if (ep in lexical_rows and ep in vector_rows) else (
                "vector" if ep in vector_rows else "lexical"
            ),
            created_at=ep.created_at,
        )
        for ep in episodes_by_id.values()
    ]

    hits.sort(key=lambda hit: (-hit.score, -hit.created_at.timestamp()))
    return hits[:limit]


async def detect_inactive_chats_for_compilation(
    session: AsyncSession,
    *,
    inactivity_hours: float = 3.0,
    limit: int = 20,
) -> list[tuple[uuid.UUID, str, str]]:
    """Find (user_id, native_user_id, native_chat_id) pairs eligible for episode compilation."""
    cutoff = datetime.now(UTC) - timedelta(hours=inactivity_hours)

    # Group completed turns by chat and user
    stmt = (
        select(
            CompletedTurn.user_id,
            UserIdentity.native_user_id,
            CompletedTurn.native_chat_id,
            func.max(CompletedTurn.occurred_at).label("last_turn_at"),
            func.count(CompletedTurn.id).label("total_turns"),
        )
        .join(UserIdentity, UserIdentity.id == CompletedTurn.user_id)
        .where(CompletedTurn.tombstoned_at.is_(None))
        .group_by(CompletedTurn.user_id, UserIdentity.native_user_id, CompletedTurn.native_chat_id)
        .having(func.max(CompletedTurn.occurred_at) <= cutoff)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()

    eligible_chats: list[tuple[uuid.UUID, str, str]] = []
    for user_id, native_user_id, native_chat_id, last_turn_at, total_turns in rows:
        # Check if there are turns newer than the latest topic episode for this chat
        latest_ep_time = (
            await session.execute(
                select(func.max(TopicEpisode.created_at)).where(
                    TopicEpisode.user_id == user_id,
                    TopicEpisode.native_chat_id == native_chat_id,
                    TopicEpisode.tombstoned_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if latest_ep_time is None or last_turn_at > latest_ep_time:
            eligible_chats.append((user_id, native_user_id, native_chat_id))

    return eligible_chats


async def get_uncompiled_turns_for_chat(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    native_chat_id: str,
) -> list[CompletedTurn]:
    """Fetch active turns for a chat that need episode compilation."""
    # Find latest episode end_message_id / time
    latest_ep_time = (
        await session.execute(
            select(func.max(TopicEpisode.created_at)).where(
                TopicEpisode.user_id == user_id,
                TopicEpisode.native_chat_id == native_chat_id,
                TopicEpisode.tombstoned_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    stmt = select(CompletedTurn).where(
        CompletedTurn.user_id == user_id,
        CompletedTurn.native_chat_id == native_chat_id,
        CompletedTurn.tombstoned_at.is_(None),
    )
    if latest_ep_time is not None:
        stmt = stmt.where(CompletedTurn.occurred_at > latest_ep_time)

    stmt = stmt.order_by(CompletedTurn.occurred_at.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def tombstone_episodes_for_chat(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    native_chat_id: str,
) -> int:
    """Tombstone all topic episodes for a deleted chat."""
    result = await session.execute(
        update(TopicEpisode)
        .where(
            TopicEpisode.user_id == user_id,
            TopicEpisode.native_chat_id == native_chat_id,
            TopicEpisode.tombstoned_at.is_(None),
        )
        .values(tombstoned_at=func.now())
    )
    return getattr(result, "rowcount", 0) or 0


async def delete_topic_episode(
    session: AsyncSession,
    *,
    episode_id: uuid.UUID,
    native_user_id: str | None = None,
) -> bool:
    """Permanently delete a topic episode."""
    stmt = delete(TopicEpisode).where(TopicEpisode.id == episode_id)
    if native_user_id:
        user_id = (
            await session.execute(
                select(UserIdentity.id).where(UserIdentity.native_user_id == native_user_id)
            )
        ).scalar_one_or_none()
        if user_id is None:
            return False
        stmt = stmt.where(TopicEpisode.user_id == user_id)

    result = await session.execute(stmt)
    return bool(getattr(result, "rowcount", 0) and getattr(result, "rowcount", 0) > 0)
