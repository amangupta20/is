import re
import uuid
from datetime import datetime
from time import perf_counter
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from assistant_core.identity.models import UserIdentity
from assistant_core.memory.consolidator import TaskModelMemoryConsolidator
from assistant_core.memory.models import (
    ConsolidationRun,
    MemoryEvidence,
    MemoryRecord,
)
from assistant_core.memory.schemas import ExplicitMemoryCandidate
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
            MemoryRecord.expires_at.is_(None) | (MemoryRecord.expires_at > func.now()),
            MemoryRecord.valid_from.is_(None) | (MemoryRecord.valid_from <= func.now()),
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
                valid_from=candidate.valid_from,
                expires_at=candidate.expires_at,
                temporal_tag=candidate.temporal_tag,
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


async def consolidate_user_memories(
    session: AsyncSession,
    *,
    native_user_id: str,
    consolidator: TaskModelMemoryConsolidator,
    trigger: str = "manual_admin",
) -> list[dict[str, Any]]:
    """Find and apply supersessions, validity updates, and reclassifications across active memories for a native user and record an audit log."""
    started_at = perf_counter()
    user_stmt = select(UserIdentity).where(UserIdentity.native_user_id == native_user_id)
    user_obj = (await session.execute(user_stmt)).scalar_one_or_none()
    user_id = user_obj.id if user_obj is not None else None

    statement = (
        select(MemoryRecord)
        .join(UserIdentity, MemoryRecord.user_id == UserIdentity.id)
        .where(
            UserIdentity.native_user_id == native_user_id,
            MemoryRecord.kind == "explicit",
            MemoryRecord.state == "active",
        )
        .order_by(MemoryRecord.created_at.asc())
    )
    records = list((await session.execute(statement)).scalars().all())
    if len(records) <= 1:
        duration_ms = (perf_counter() - started_at) * 1000
        run_log = ConsolidationRun(
            id=uuid.uuid4(),
            user_id=user_id,
            native_user_id=native_user_id,
            trigger=trigger,
            status="no_changes",
            memories_scanned=len(records),
            superseded_count=0,
            details=[],
            duration_ms=round(duration_ms, 2),
        )
        session.add(run_log)
        return []

    memory_dicts = [
        {
            "id": r.id,
            "key": r.key,
            "category": r.category,
            "statement": r.statement,
            "temporal_tag": r.temporal_tag or "permanent",
            "expires_at": r.expires_at.isoformat() if r.expires_at else "none",
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in records
    ]

    result = consolidator.consolidate(memory_dicts)
    record_by_id = {r.id: r for r in records}
    applied: list[dict[str, Any]] = []

    # 1. Process Supersessions
    for s in result.supersessions:
        superseded = record_by_id.get(s.superseded_id)
        superseding = record_by_id.get(s.superseded_by_id)

        if not superseded or not superseding:
            continue
        if superseded.id == superseding.id:
            continue
        if superseded.state != "active" or superseding.state != "active":
            continue

        await session.execute(
            update(MemoryRecord)
            .where(MemoryRecord.id == superseded.id)
            .values(
                state="superseded",
                superseded_at=func.now(),
                superseded_by_id=superseding.id,
            )
        )
        superseded.state = "superseded"
        applied.append(
            {
                "type": "supersession",
                "superseded_id": str(superseded.id),
                "superseded_statement": superseded.statement,
                "superseded_by_id": str(superseding.id),
                "superseding_statement": superseding.statement,
                "reason": s.reason,
            }
        )

    # 2. Process Validity Updates
    for v in result.validity_updates:
        rec = record_by_id.get(v.memory_id)
        if not rec or rec.state != "active":
            continue

        expires_at_dt: datetime | None = None
        if v.expires_at and v.expires_at.lower() not in ("none", "null", ""):
            try:
                expires_at_dt = datetime.fromisoformat(v.expires_at)
            except ValueError:
                expires_at_dt = None

        if v.action == "expire_now":
            await session.execute(
                update(MemoryRecord).where(MemoryRecord.id == rec.id).values(state="expired")
            )
            rec.state = "expired"
            applied.append(
                {
                    "type": "validity_update",
                    "action": "expire_now",
                    "memory_id": str(rec.id),
                    "statement": rec.statement,
                    "old_validity": (
                        rec.expires_at.isoformat() if rec.expires_at else "permanent"
                    ),
                    "new_validity": "expired",
                    "reason": v.reason,
                }
            )
        elif v.action in ("set_expiration", "extend_expiration"):
            await session.execute(
                update(MemoryRecord)
                .where(MemoryRecord.id == rec.id)
                .values(expires_at=expires_at_dt, temporal_tag=v.temporal_tag)
            )
            old_val = rec.expires_at.isoformat() if rec.expires_at else "permanent"
            rec.expires_at = expires_at_dt
            rec.temporal_tag = v.temporal_tag
            applied.append(
                {
                    "type": "validity_update",
                    "action": v.action,
                    "memory_id": str(rec.id),
                    "statement": rec.statement,
                    "old_validity": old_val,
                    "new_validity": expires_at_dt.isoformat() if expires_at_dt else "none",
                    "temporal_tag": v.temporal_tag,
                    "reason": v.reason,
                }
            )
        elif v.action == "mark_permanent":
            await session.execute(
                update(MemoryRecord)
                .where(MemoryRecord.id == rec.id)
                .values(expires_at=None, temporal_tag=None)
            )
            old_val = rec.expires_at.isoformat() if rec.expires_at else "none"
            rec.expires_at = None
            rec.temporal_tag = None
            applied.append(
                {
                    "type": "validity_update",
                    "action": "mark_permanent",
                    "memory_id": str(rec.id),
                    "statement": rec.statement,
                    "old_validity": old_val,
                    "new_validity": "permanent",
                    "reason": v.reason,
                }
            )

    # 3. Process Reclassifications
    for r in result.reclassifications:
        rec = record_by_id.get(r.memory_id)
        if not rec or rec.state != "active":
            continue
        clean_cat = r.new_category.strip().lower().replace(" ", "_")[:50]
        if len(clean_cat) < 2 or rec.category == clean_cat:
            continue
        old_cat = rec.category
        await session.execute(
            update(MemoryRecord).where(MemoryRecord.id == rec.id).values(category=clean_cat)
        )
        rec.category = clean_cat
        applied.append(
            {
                "type": "reclassification",
                "memory_id": str(rec.id),
                "statement": rec.statement,
                "old_category": old_cat,
                "new_category": clean_cat,
                "reason": r.reason,
            }
        )

    duration_ms = (perf_counter() - started_at) * 1000
    run_log = ConsolidationRun(
        id=uuid.uuid4(),
        user_id=user_id,
        native_user_id=native_user_id,
        trigger=trigger,
        status="success" if applied else "no_changes",
        memories_scanned=len(records),
        superseded_count=len(applied),
        details=applied,
        duration_ms=round(duration_ms, 2),
    )
    session.add(run_log)
    return applied
