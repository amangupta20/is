"""Idempotent application of source-linked explicit-memory candidates."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.memory.models import MemoryEvidence, MemoryRecord
from assistant_core.memory.schemas import ExplicitMemoryCandidate
from assistant_core.turns.models import CompletedTurn


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
                    superseded_by_id=replacement_id,
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
        await _insert_evidence(session, record, turn, candidate.evidence_quote)
        applied.append(record)
    return applied
