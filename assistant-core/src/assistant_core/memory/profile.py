"""Build and freeze the cache-stable profile prefix for one native chat."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.identity.models import UserIdentity
from assistant_core.memory.models import ChatProfileSnapshot, MemoryRecord
from assistant_core.memory.repository import live_memory_evidence_clause


def _render_profile(
    records: Sequence[MemoryRecord],
    *,
    max_chars: int,
) -> tuple[str, list[uuid.UUID]]:
    """Render whole memory statements without exceeding the fixed character budget."""
    prefix = "<user_profile>\n"
    suffix = "\n</user_profile>"
    lines: list[str] = []
    source_ids: list[uuid.UUID] = []

    for record in records:
        statement = " ".join(record.statement.split())
        if not statement:
            continue
        line = f"- {statement}"
        candidate = prefix + "\n".join([*lines, line]) + suffix
        if len(candidate) > max_chars:
            continue
        lines.append(line)
        source_ids.append(record.id)

    if not lines:
        return "", []
    return prefix + "\n".join(lines) + suffix, source_ids


async def get_or_create_profile(
    session: AsyncSession,
    *,
    native_user_id: str,
    native_chat_id: str,
    max_chars: int = 8_000,
) -> ChatProfileSnapshot:
    """Return the immutable profile snapshot created on a chat's first request."""
    identity_insert = insert(UserIdentity).values(
        id=uuid.uuid4(),
        native_user_id=native_user_id,
    )
    identity_upsert = identity_insert.on_conflict_do_update(
        index_elements=[UserIdentity.native_user_id],
        set_={"native_user_id": identity_insert.excluded.native_user_id},
    ).returning(UserIdentity.id)
    user_id = (await session.execute(identity_upsert)).scalar_one()

    snapshot_query = select(ChatProfileSnapshot).where(
        ChatProfileSnapshot.user_id == user_id,
        ChatProfileSnapshot.native_chat_id == native_chat_id,
    )
    existing = (await session.execute(snapshot_query)).scalar_one_or_none()
    if existing is not None:
        return existing

    memory_query = (
        select(MemoryRecord)
        .where(
            MemoryRecord.user_id == user_id,
            MemoryRecord.state == "active",
            MemoryRecord.category.in_(("preference", "instruction")),
            MemoryRecord.expires_at.is_(None) | (MemoryRecord.expires_at > func.now()),
            MemoryRecord.valid_from.is_(None) | (MemoryRecord.valid_from <= func.now()),
            live_memory_evidence_clause(),
        )
        .order_by(MemoryRecord.category.asc(), MemoryRecord.key.asc(), MemoryRecord.id.asc())
    )
    records = (await session.execute(memory_query)).scalars().all()
    rendered_text, source_memory_ids = _render_profile(records, max_chars=max_chars)

    snapshot_insert = (
        insert(ChatProfileSnapshot)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            native_chat_id=native_chat_id,
            rendered_text=rendered_text,
            source_memory_ids=source_memory_ids,
        )
        .on_conflict_do_nothing(
            index_elements=[
                ChatProfileSnapshot.user_id,
                ChatProfileSnapshot.native_chat_id,
            ]
        )
        .returning(ChatProfileSnapshot)
    )
    created = (await session.execute(snapshot_insert)).scalar_one_or_none()
    if created is not None:
        return created

    return (await session.execute(snapshot_query)).scalar_one()
