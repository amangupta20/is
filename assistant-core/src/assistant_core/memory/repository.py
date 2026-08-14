"""Application and inspection of source-linked explicit-memory candidates."""

import re
import uuid

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from assistant_core.identity.models import UserIdentity
from assistant_core.memory.models import MemoryEvidence, MemoryRecord
from assistant_core.memory.schemas import ExplicitMemoryCandidate, UserMemoryItem
from assistant_core.turns.models import CompletedTurn


def _normalized_tokens(value: str) -> tuple[str, ...]:
    """Normalize visible text into comparable lexical tokens."""
    return tuple(re.findall(r"\w+", " ".join(value.split()).casefold()))


def live_memory_evidence_clause() -> ColumnElement[bool]:
    """Require at least one owner-matched evidence turn that is not tombstoned."""
    return exists(
        select(MemoryEvidence.id)
        .join(CompletedTurn, CompletedTurn.id == MemoryEvidence.completed_turn_id)
        .where(
            MemoryEvidence.memory_record_id == MemoryRecord.id,
            CompletedTurn.user_id == MemoryRecord.user_id,
            CompletedTurn.tombstoned_at.is_(None),
        )
    )


async def search_explicit_memory(
    session: AsyncSession,
    *,
    native_user_id: str,
    query: str,
    limit: int,
) -> list[MemoryRecord]:
    """Return active explicit records ranked by normalized exact/token overlap."""
    normalized_query = " ".join(query.split()).casefold()
    query_tokens = set(_normalized_tokens(normalized_query))
    if not query_tokens:
        return []

    statement = (
        select(MemoryRecord)
        .join(UserIdentity, MemoryRecord.user_id == UserIdentity.id)
        .where(
            UserIdentity.native_user_id == native_user_id,
            MemoryRecord.kind == "explicit",
            MemoryRecord.state == "active",
            live_memory_evidence_clause(),
        )
    )
    records = (await session.execute(statement)).scalars().all()
    ranked: list[tuple[int, int, str, MemoryRecord]] = []
    for record in records:
        normalized_statement = " ".join(record.statement.split()).casefold()
        overlap = len(query_tokens.intersection(_normalized_tokens(normalized_statement)))
        if overlap == 0:
            continue
        ranked.append(
            (
                int(normalized_statement == normalized_query),
                overlap,
                str(record.id),
                record,
            )
        )
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [record for *_score, record in ranked[:limit]]


async def read_explicit_memory(
    session: AsyncSession,
    *,
    native_user_id: str,
    memory_source_id: uuid.UUID,
) -> tuple[MemoryRecord, MemoryEvidence, CompletedTurn] | None:
    """Read one active source only when its record belongs to the native user."""
    statement = (
        select(MemoryRecord, MemoryEvidence, CompletedTurn)
        .join(MemoryEvidence, MemoryEvidence.memory_record_id == MemoryRecord.id)
        .join(CompletedTurn, CompletedTurn.id == MemoryEvidence.completed_turn_id)
        .join(UserIdentity, MemoryRecord.user_id == UserIdentity.id)
        .where(
            UserIdentity.native_user_id == native_user_id,
            MemoryRecord.id == memory_source_id,
            MemoryRecord.kind == "explicit",
            MemoryRecord.state == "active",
            CompletedTurn.user_id == MemoryRecord.user_id,
            CompletedTurn.tombstoned_at.is_(None),
        )
        .order_by(MemoryEvidence.created_at.asc(), MemoryEvidence.id.asc())
        .limit(1)
    )
    result = (await session.execute(statement)).one_or_none()
    if result is None:
        return None
    return result[0], result[1], result[2]


async def _active_record(
    session: AsyncSession, turn: CompletedTurn, key: str
) -> MemoryRecord | None:
    """Lock the current record for one user/key before changing its state."""
    statement = (
        select(MemoryRecord)
        .where(
            MemoryRecord.user_id == turn.user_id,
            MemoryRecord.key == key,
            MemoryRecord.state == "active",
        )
        .with_for_update()
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _has_evidence(
    session: AsyncSession, record: MemoryRecord, turn: CompletedTurn, quote: str
) -> bool:
    """Return whether this exact source quote already supports the record."""
    statement = select(MemoryEvidence.id).where(
        MemoryEvidence.memory_record_id == record.id,
        MemoryEvidence.completed_turn_id == turn.id,
        MemoryEvidence.native_user_message_id == turn.native_user_message_id,
        MemoryEvidence.evidence_quote == quote,
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


async def _insert_evidence(
    session: AsyncSession, record: MemoryRecord, turn: CompletedTurn, quote: str
) -> None:
    """Persist the source provenance only after its quoted text was verified."""
    statement = insert(MemoryEvidence).values(
        memory_record_id=record.id,
        completed_turn_id=turn.id,
        native_user_message_id=turn.native_user_message_id,
        evidence_quote=quote,
    )
    await session.execute(statement)


async def apply_explicit_candidates(
    session: AsyncSession,
    turn: CompletedTurn,
    candidates: list[ExplicitMemoryCandidate],
) -> list[MemoryRecord]:
    """Apply exact-quote candidates once, retaining prior facts on correction."""
    if any(candidate.evidence_quote not in turn.user_content for candidate in candidates):
        raise ValueError("memory evidence quote is not present in the user content")

    applied: list[MemoryRecord] = []
    for candidate in candidates:
        active = await _active_record(session, turn, candidate.key)
        if active is not None and active.statement == candidate.statement:
            if await _has_evidence(session, active, turn, candidate.evidence_quote):
                continue
            await _insert_evidence(session, active, turn, candidate.evidence_quote)
            applied.append(active)
            continue

        replacement_id: uuid.UUID | None = None
        if active is not None:
            replacement_id = uuid.uuid4()
            await session.execute(
                update(MemoryRecord)
                .where(MemoryRecord.id == active.id)
                .values(
                    state="superseded",
                    superseded_at=func.now(),
                )
            )

        statement = (
            insert(MemoryRecord)
            .values(
                id=replacement_id or uuid.uuid4(),
                user_id=turn.user_id,
                key=candidate.key,
                category=candidate.category,
                statement=candidate.statement,
                kind="explicit",
                confidence=1,
                state="active",
            )
            .returning(MemoryRecord)
        )
        record = (await session.execute(statement)).scalar_one_or_none()
        if not isinstance(record, MemoryRecord):
            raise TypeError("memory record insertion did not return a record")
        if active is not None:
            await session.execute(
                update(MemoryRecord)
                .where(MemoryRecord.id == active.id)
                .values(superseded_by_id=replacement_id)
            )
        await _insert_evidence(session, record, turn, candidate.evidence_quote)
        applied.append(record)
    return applied


async def list_user_memories(
    session: AsyncSession,
    *,
    native_user_id: str,
    status: str = "active",
    category: str | None = None,
    limit: int = 100,
) -> list[UserMemoryItem]:
    """List structured memories for one native user filtered by status/category."""
    record_query = (
        select(MemoryRecord)
        .join(UserIdentity, MemoryRecord.user_id == UserIdentity.id)
        .where(UserIdentity.native_user_id == native_user_id)
    )
    if status and status.lower() != "all":
        record_query = record_query.where(MemoryRecord.state == status.lower())
    if category and category.lower() != "all":
        record_query = record_query.where(MemoryRecord.category == category.lower())

    record_query = record_query.order_by(
        MemoryRecord.updated_at.desc(),
        MemoryRecord.created_at.desc(),
    ).limit(limit)

    records = (await session.execute(record_query)).scalars().all()
    if not records:
        return []

    record_ids = [record.id for record in records]
    evidence_query = (
        select(MemoryEvidence, CompletedTurn)
        .outerjoin(CompletedTurn, CompletedTurn.id == MemoryEvidence.completed_turn_id)
        .where(MemoryEvidence.memory_record_id.in_(record_ids))
        .order_by(MemoryEvidence.created_at.asc(), MemoryEvidence.id.asc())
    )
    evidence_rows = (await session.execute(evidence_query)).all()

    evidence_by_record: dict[uuid.UUID, tuple[MemoryEvidence, CompletedTurn | None]] = {}
    for row in evidence_rows:
        ev: MemoryEvidence = row[0]
        turn: CompletedTurn | None = row[1] if len(row) > 1 else None
        if ev.memory_record_id not in evidence_by_record:
            evidence_by_record[ev.memory_record_id] = (ev, turn)

    results: list[UserMemoryItem] = []
    for record in records:
        ev_turn = evidence_by_record.get(record.id)
        evidence = ev_turn[0] if ev_turn else None
        turn = ev_turn[1] if ev_turn else None

        results.append(
            UserMemoryItem(
                id=str(record.id),
                key=record.key,
                category=record.category,
                statement=record.statement,
                state=record.state,
                confidence=record.confidence,
                created_at=record.created_at,
                updated_at=record.updated_at,
                archived_at=record.archived_at,
                superseded_at=record.superseded_at,
                superseded_by_id=str(record.superseded_by_id) if record.superseded_by_id else None,
                evidence_quote=evidence.evidence_quote if evidence else None,
                native_chat_id=turn.native_chat_id if turn else None,
                native_message_id=evidence.native_user_message_id if evidence else (turn.native_user_message_id if turn else None),
            )
        )
    return results


async def archive_user_memory(
    session: AsyncSession,
    *,
    native_user_id: str,
    memory_id: uuid.UUID,
) -> MemoryRecord | None:
    """Find and archive one user-owned memory record."""
    statement = (
        select(MemoryRecord)
        .join(UserIdentity, MemoryRecord.user_id == UserIdentity.id)
        .where(
            UserIdentity.native_user_id == native_user_id,
            MemoryRecord.id == memory_id,
        )
        .with_for_update()
    )
    record = (await session.execute(statement)).scalar_one_or_none()
    if record is None:
        return None

    update_stmt = (
        update(MemoryRecord)
        .where(MemoryRecord.id == record.id)
        .values(
            state="archived",
            archived_at=func.now(),
            updated_at=func.now(),
        )
        .returning(MemoryRecord)
    )
    updated_record = (await session.execute(update_stmt)).scalar_one_or_none()
    return updated_record or record


async def update_user_memory(
    session: AsyncSession,
    *,
    native_user_id: str,
    memory_id: uuid.UUID,
    new_statement: str,
) -> MemoryRecord | None:
    """Update the statement of one user-owned memory record."""
    statement = (
        select(MemoryRecord)
        .join(UserIdentity, MemoryRecord.user_id == UserIdentity.id)
        .where(
            UserIdentity.native_user_id == native_user_id,
            MemoryRecord.id == memory_id,
        )
        .with_for_update()
    )
    record = (await session.execute(statement)).scalar_one_or_none()
    if record is None:
        return None

    update_stmt = (
        update(MemoryRecord)
        .where(MemoryRecord.id == record.id)
        .values(
            statement=new_statement,
            updated_at=func.now(),
        )
        .returning(MemoryRecord)
    )
    updated_record = (await session.execute(update_stmt)).scalar_one_or_none()
    return updated_record or record


async def merge_user_memories(
    session: AsyncSession,
    *,
    native_user_id: str,
    source_memory_ids: list[uuid.UUID],
    target_category: str,
    new_statement: str,
) -> tuple[MemoryRecord, list[uuid.UUID]] | None:
    """Consolidate multiple user-owned memories into one new active record."""
    unique_source_ids = list(dict.fromkeys(source_memory_ids))
    if not unique_source_ids:
        return None

    statement = (
        select(MemoryRecord)
        .join(UserIdentity, MemoryRecord.user_id == UserIdentity.id)
        .where(
            UserIdentity.native_user_id == native_user_id,
            MemoryRecord.id.in_(unique_source_ids),
        )
        .with_for_update()
    )
    source_records = (await session.execute(statement)).scalars().all()
    if len(source_records) != len(unique_source_ids):
        return None

    user_id = source_records[0].user_id
    new_id = uuid.uuid4()
    new_key = f"merged.m_{new_id.hex[:8]}"

    insert_stmt = (
        insert(MemoryRecord)
        .values(
            id=new_id,
            user_id=user_id,
            key=new_key,
            category=target_category,
            statement=new_statement,
            kind="explicit",
            confidence=1,
            state="active",
        )
        .returning(MemoryRecord)
    )
    new_record = (await session.execute(insert_stmt)).scalar_one_or_none()
    if not isinstance(new_record, MemoryRecord):
        raise TypeError("memory record insertion did not return a record")

    evidence_stmt = (
        select(MemoryEvidence)
        .where(MemoryEvidence.memory_record_id.in_(unique_source_ids))
        .order_by(MemoryEvidence.created_at.asc(), MemoryEvidence.id.asc())
        .limit(1)
    )
    first_evidence = (await session.execute(evidence_stmt)).scalar_one_or_none()
    if first_evidence is not None:
        new_evidence_stmt = insert(MemoryEvidence).values(
            id=uuid.uuid4(),
            memory_record_id=new_record.id,
            completed_turn_id=first_evidence.completed_turn_id,
            native_user_message_id=first_evidence.native_user_message_id,
            evidence_quote=first_evidence.evidence_quote,
        )
        await session.execute(new_evidence_stmt)

    archive_stmt = (
        update(MemoryRecord)
        .where(MemoryRecord.id.in_(unique_source_ids))
        .values(
            state="archived",
            archived_at=func.now(),
            superseded_by_id=new_record.id,
            updated_at=func.now(),
        )
    )
    await session.execute(archive_stmt)

    return new_record, unique_source_ids

