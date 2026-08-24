import re
import uuid
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import structlog
from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.elements import ColumnElement

from assistant_core.identity.models import UserIdentity
from assistant_core.memory.consolidator import TaskModelMemoryConsolidator
from assistant_core.memory.models import (
    ConsolidationRun,
    MemoryChangeLog,
    MemoryEvidence,
    MemoryRecord,
)
from assistant_core.memory.schemas import ExplicitMemoryCandidate
from assistant_core.turns.models import CompletedTurn

LOGGER = structlog.get_logger("assistant_core.memory.repository")


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
    """Return active explicit and inferred records ranked by normalized exact/token overlap."""
    normalized_query = " ".join(query.split()).casefold()
    query_tokens = set(_normalized_tokens(normalized_query))
    if not query_tokens:
        return []

    statement = (
        select(MemoryRecord)
        .join(UserIdentity, MemoryRecord.user_id == UserIdentity.id)
        .where(
            UserIdentity.native_user_id == native_user_id,
            MemoryRecord.kind.in_(("explicit", "inferred")),
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
            MemoryRecord.kind.in_(("explicit", "inferred")),
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


def _memory_snapshot(record: MemoryRecord | None) -> dict[str, Any] | None:
    """Serialize a snapshot of memory record state for audit and rollbacks."""
    if record is None:
        return None
    return {
        "id": str(record.id),
        "key": record.key,
        "category": record.category,
        "statement": record.statement,
        "state": record.state,
        "kind": record.kind,
        "confidence": record.confidence,
        "temporal_tag": record.temporal_tag,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "valid_from": record.valid_from.isoformat() if record.valid_from else None,
    }


async def log_memory_change(
    session: AsyncSession,
    *,
    native_user_id: str = "",
    user_id: uuid.UUID | None = None,
    memory_id: uuid.UUID | None = None,
    change_source: str,
    action: str,
    previous_state: dict[str, Any] | None = None,
    new_state: dict[str, Any] | None = None,
    reason: str | None = None,
) -> MemoryChangeLog:
    """Persist an audit log entry for memory state mutations."""
    log_entry = MemoryChangeLog(
        id=uuid.uuid4(),
        user_id=user_id,
        native_user_id=native_user_id or "user",
        memory_id=memory_id,
        change_source=change_source,
        action=action,
        previous_state=previous_state,
        new_state=new_state,
        reason=reason,
        is_reverted=False,
        reverted_at=None,
        created_at=datetime.now(UTC),
    )
    session.add(log_entry)
    return log_entry


async def _apply_inferred_candidate(
    session: AsyncSession,
    turn: CompletedTurn,
    candidate: ExplicitMemoryCandidate,
) -> MemoryRecord | None:
    """Store or refresh one tentative inferred pattern without touching confirmed records."""
    active = await _active_record(session, turn, candidate.key)
    if active is not None and active.kind == "explicit":
        return None

    if active is not None:
        prev_snap = _memory_snapshot(active)
        changed = False
        if active.statement != candidate.statement:
            active.statement = candidate.statement
            active.category = candidate.category
            changed = True
        if not await _has_evidence(session, active, turn, candidate.evidence_quote):
            await _insert_evidence(session, active, turn, candidate.evidence_quote)
        if changed:
            await session.flush()
            await log_memory_change(
                session,
                user_id=turn.user_id,
                memory_id=active.id,
                change_source="turn_extraction",
                action="update",
                previous_state=prev_snap,
                new_state=_memory_snapshot(active),
                reason=(
                    "Refreshed inferred pattern from conversation evidence: "
                    f'"{candidate.evidence_quote[:100]}"'
                ),
            )
        return active

    statement = (
        insert(MemoryRecord)
        .values(
            id=uuid.uuid4(),
            user_id=turn.user_id,
            key=candidate.key,
            category=candidate.category,
            statement=candidate.statement,
            kind="inferred",
            confidence=0,
            state="active",
        )
        .returning(MemoryRecord)
    )
    record = (await session.execute(statement)).scalar_one_or_none()
    if not isinstance(record, MemoryRecord):
        raise TypeError("memory record insertion did not return a record")
    await _insert_evidence(session, record, turn, candidate.evidence_quote)

    await log_memory_change(
        session,
        user_id=turn.user_id,
        memory_id=record.id,
        change_source="turn_extraction",
        action="create",
        previous_state=None,
        new_state=_memory_snapshot(record),
        reason=f'Inferred from conversation evidence: "{candidate.evidence_quote[:100]}"',
    )
    return record


def _normalized_evidence_text(value: str) -> str:
    """Normalize text so cosmetic whitespace/case drift does not break matching."""
    return " ".join(value.split()).casefold()


async def apply_explicit_candidates(
    session: AsyncSession,
    turn: CompletedTurn,
    candidates: list[ExplicitMemoryCandidate],
) -> list[MemoryRecord]:
    """Apply evidence-backed candidates once; explicit records supersede, inferred accumulate.

    Candidates whose quote is absent from the turn (even after normalization)
    are dropped with a warning instead of aborting the whole extraction.
    """
    full_turn_normalized = _normalized_evidence_text(
        f"{turn.user_content}\n{turn.assistant_content}"
    )
    valid: list[ExplicitMemoryCandidate] = []
    dropped = 0
    for candidate in candidates:
        if _normalized_evidence_text(candidate.evidence_quote) in full_turn_normalized:
            valid.append(candidate)
        else:
            dropped += 1
    if dropped:
        LOGGER.warning(
            "memory_candidates_dropped_missing_evidence",
            dropped=dropped,
            kept=len(valid),
        )
    if not valid:
        return []

    applied: list[MemoryRecord] = []
    ordered = sorted(valid, key=lambda c: 0 if c.kind == "explicit" else 1)
    for candidate in ordered:
        if candidate.kind == "inferred":
            record = await _apply_inferred_candidate(session, turn, candidate)
            if record is not None:
                applied.append(record)
            continue

        active = await _active_record(session, turn, candidate.key)
        if active is not None and active.statement == candidate.statement:
            if await _has_evidence(session, active, turn, candidate.evidence_quote):
                continue
            await _insert_evidence(session, active, turn, candidate.evidence_quote)
            applied.append(active)
            continue

        replacement_id: uuid.UUID | None = None
        active_snap = _memory_snapshot(active) if active is not None else None
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

        await log_memory_change(
            session,
            user_id=turn.user_id,
            memory_id=record.id,
            change_source="turn_extraction",
            action="supersede" if active is not None else "create",
            previous_state=active_snap,
            new_state=_memory_snapshot(record),
            reason=f'Extracted from conversation evidence: "{candidate.evidence_quote[:100]}"',
        )
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
            MemoryRecord.kind.in_(("explicit", "inferred")),
            MemoryRecord.state == "active",
        )
        .order_by(MemoryRecord.created_at.asc())
    )
    records = list((await session.execute(statement)).scalars().all())
    has_inferred = any(record.kind == "inferred" for record in records)
    if len(records) <= 1 and not has_inferred:
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

    evidence_counts: dict[uuid.UUID, int] = {}
    if records:
        counts_stmt = (
            select(MemoryEvidence.memory_record_id, func.count(MemoryEvidence.id))
            .where(MemoryEvidence.memory_record_id.in_([record.id for record in records]))
            .group_by(MemoryEvidence.memory_record_id)
        )
        evidence_counts = {
            record_id: int(count) for record_id, count in (await session.execute(counts_stmt)).all()
        }

    memory_dicts = [
        {
            "id": r.id,
            "key": r.key,
            "kind": r.kind,
            "evidence_count": evidence_counts.get(r.id, 0),
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
        superseded_snap = _memory_snapshot(superseded)
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
        await log_memory_change(
            session,
            native_user_id=native_user_id,
            user_id=user_id,
            memory_id=superseded.id,
            change_source="consolidation",
            action="supersede",
            previous_state=superseded_snap,
            new_state=_memory_snapshot(superseding),
            reason=s.reason,
        )

    # 2. Process Validity Updates
    for v in result.validity_updates:
        rec = record_by_id.get(v.memory_id)
        if not rec or rec.state != "active":
            continue

        rec_snap = _memory_snapshot(rec)
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
                    "old_validity": (rec.expires_at.isoformat() if rec.expires_at else "permanent"),
                    "new_validity": "expired",
                    "reason": v.reason,
                }
            )
            await log_memory_change(
                session,
                native_user_id=native_user_id,
                user_id=user_id,
                memory_id=rec.id,
                change_source="consolidation",
                action="validity_change",
                previous_state=rec_snap,
                new_state=_memory_snapshot(rec),
                reason=v.reason,
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
            await log_memory_change(
                session,
                native_user_id=native_user_id,
                user_id=user_id,
                memory_id=rec.id,
                change_source="consolidation",
                action="validity_change",
                previous_state=rec_snap,
                new_state=_memory_snapshot(rec),
                reason=v.reason,
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
            await log_memory_change(
                session,
                native_user_id=native_user_id,
                user_id=user_id,
                memory_id=rec.id,
                change_source="consolidation",
                action="validity_change",
                previous_state=rec_snap,
                new_state=_memory_snapshot(rec),
                reason=v.reason,
            )

    # 3. Process Reclassifications
    for r in result.reclassifications:
        rec = record_by_id.get(r.memory_id)
        if not rec or rec.state != "active":
            continue
        clean_cat = r.new_category.strip().lower().replace(" ", "_")[:50]
        if len(clean_cat) < 2 or rec.category == clean_cat:
            continue
        rec_snap = _memory_snapshot(rec)
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
        await log_memory_change(
            session,
            native_user_id=native_user_id,
            user_id=user_id,
            memory_id=rec.id,
            change_source="consolidation",
            action="reclassify",
            previous_state=rec_snap,
            new_state=_memory_snapshot(rec),
            reason=r.reason,
        )

    # 4. Process Inferred Promotions
    for p in result.promotions:
        rec = record_by_id.get(p.memory_id)
        if rec is None or rec.state != "active" or rec.kind != "inferred":
            continue
        rec_snap = _memory_snapshot(rec)
        await session.execute(
            update(MemoryRecord)
            .where(MemoryRecord.id == rec.id)
            .values(kind="explicit", confidence=1)
        )
        rec.kind = "explicit"
        rec.confidence = 1
        applied.append(
            {
                "type": "promotion",
                "memory_id": str(rec.id),
                "statement": rec.statement,
                "reason": p.reason,
            }
        )
        await log_memory_change(
            session,
            native_user_id=native_user_id,
            user_id=user_id,
            memory_id=rec.id,
            change_source="consolidation",
            action="promote",
            previous_state=rec_snap,
            new_state=_memory_snapshot(rec),
            reason=p.reason,
        )

    # 5. Process Inferred Discards
    for d in result.discards:
        rec = record_by_id.get(d.memory_id)
        if rec is None or rec.state != "active" or rec.kind != "inferred":
            continue
        rec_snap = _memory_snapshot(rec)
        await session.execute(
            update(MemoryRecord)
            .where(MemoryRecord.id == rec.id)
            .values(state="archived", archived_at=func.now())
        )
        rec.state = "archived"
        applied.append(
            {
                "type": "discard",
                "memory_id": str(rec.id),
                "statement": rec.statement,
                "reason": d.reason,
            }
        )
        await log_memory_change(
            session,
            native_user_id=native_user_id,
            user_id=user_id,
            memory_id=rec.id,
            change_source="consolidation",
            action="archive",
            previous_state=rec_snap,
            new_state=_memory_snapshot(rec),
            reason=d.reason,
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


async def list_user_memories(
    session: AsyncSession,
    *,
    native_user_id: str,
    query: str | None = None,
    category: str | None = None,
    status: str = "active",
    limit: int = 20,
) -> list[MemoryRecord]:
    """List memory records for a user with optional query and category filters."""
    user_stmt = select(UserIdentity.id).where(UserIdentity.native_user_id == native_user_id)
    user_id = (await session.execute(user_stmt)).scalar_one_or_none()
    if user_id is None:
        return []

    stmt = (
        select(MemoryRecord)
        .where(
            MemoryRecord.user_id == user_id,
            MemoryRecord.state == status,
        )
        .order_by(MemoryRecord.updated_at.desc(), MemoryRecord.created_at.desc())
    )

    if category and category.strip():
        stmt = stmt.where(MemoryRecord.category == category.strip().lower())

    if query and query.strip():
        like_pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            MemoryRecord.statement.ilike(like_pattern) | MemoryRecord.key.ilike(like_pattern)
        )

    stmt = stmt.limit(min(max(1, limit), 100))
    records = list((await session.execute(stmt)).scalars().all())
    return records


async def save_direct_memory(
    session: AsyncSession,
    *,
    native_user_id: str,
    key: str,
    statement: str,
    category: str = "fact",
    temporal_tag: str | None = None,
    expires_at: datetime | None = None,
    change_source: str = "chat_tool",
) -> tuple[MemoryRecord, bool]:
    """Create or supersede an explicit memory directly via tool invocation."""
    user_stmt = select(UserIdentity).where(UserIdentity.native_user_id == native_user_id)
    user = (await session.execute(user_stmt)).scalar_one_or_none()
    if user is None:
        user = UserIdentity(id=uuid.uuid4(), native_user_id=native_user_id)
        session.add(user)
        await session.flush()

    clean_key = ".".join(re.findall(r"[a-z0-9_-]+", key.strip().lower())) or "general.note"
    clean_cat = "".join(re.findall(r"[a-z0-9_-]+", category.strip().lower()))[:50] or "fact"
    clean_tag = temporal_tag.strip().lower() if temporal_tag and temporal_tag.strip() else None

    # Check for existing active record with same key
    existing_stmt = (
        select(MemoryRecord)
        .where(
            MemoryRecord.user_id == user.id,
            MemoryRecord.key == clean_key,
            MemoryRecord.state == "active",
        )
        .with_for_update()
    )
    active_rec = (await session.execute(existing_stmt)).scalar_one_or_none()

    if active_rec is not None and active_rec.statement == statement.strip():
        prev_snap = _memory_snapshot(active_rec)
        active_rec.category = clean_cat
        active_rec.temporal_tag = clean_tag
        active_rec.expires_at = expires_at
        await log_memory_change(
            session,
            native_user_id=native_user_id,
            user_id=user.id,
            memory_id=active_rec.id,
            change_source=change_source,
            action="update",
            previous_state=prev_snap,
            new_state=_memory_snapshot(active_rec),
            reason="Updated metadata for existing memory key",
        )
        return active_rec, False

    replacement_id = uuid.uuid4()
    active_snap = _memory_snapshot(active_rec) if active_rec is not None else None
    if active_rec is not None:
        await session.execute(
            update(MemoryRecord)
            .where(MemoryRecord.id == active_rec.id)
            .values(
                state="superseded",
                superseded_at=func.now(),
                superseded_by_id=replacement_id,
            )
        )

    new_rec = MemoryRecord(
        id=replacement_id,
        user_id=user.id,
        key=clean_key,
        category=clean_cat,
        statement=statement.strip(),
        temporal_tag=clean_tag,
        expires_at=expires_at,
        kind="explicit",
        confidence=1,
        state="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(new_rec)
    await session.flush()

    await log_memory_change(
        session,
        native_user_id=native_user_id,
        user_id=user.id,
        memory_id=new_rec.id,
        change_source=change_source,
        action="supersede" if active_rec is not None else "create",
        previous_state=active_snap,
        new_state=_memory_snapshot(new_rec),
        reason=f"{'Superseded' if active_rec is not None else 'Saved'} via {change_source}",
    )
    return new_rec, True


async def update_direct_memory(
    session: AsyncSession,
    *,
    native_user_id: str,
    memory_id: uuid.UUID,
    statement: str | None = None,
    category: str | None = None,
    temporal_tag: str | None = None,
    expires_at: datetime | None = None,
    clear_expiration: bool = False,
    change_source: str = "chat_tool",
) -> MemoryRecord | None:
    """Modify an active memory record's statement, category, or temporal metadata."""
    user_stmt = select(UserIdentity.id).where(UserIdentity.native_user_id == native_user_id)
    user_id = (await session.execute(user_stmt)).scalar_one_or_none()
    if user_id is None:
        return None

    stmt = (
        select(MemoryRecord)
        .where(
            MemoryRecord.id == memory_id,
            MemoryRecord.user_id == user_id,
            MemoryRecord.state == "active",
        )
        .with_for_update()
    )
    rec = (await session.execute(stmt)).scalar_one_or_none()
    if rec is None:
        return None

    prev_snap = _memory_snapshot(rec)
    if statement and statement.strip():
        rec.statement = statement.strip()
    if category and category.strip():
        clean_cat = "".join(re.findall(r"[a-z0-9_-]+", category.strip().lower()))[:50]
        if len(clean_cat) >= 2:
            rec.category = clean_cat
    if temporal_tag is not None:
        rec.temporal_tag = temporal_tag.strip().lower() if temporal_tag.strip() else None
    if clear_expiration:
        rec.expires_at = None
    elif expires_at is not None:
        rec.expires_at = expires_at

    await session.flush()
    await log_memory_change(
        session,
        native_user_id=native_user_id,
        user_id=user_id,
        memory_id=rec.id,
        change_source=change_source,
        action="update",
        previous_state=prev_snap,
        new_state=_memory_snapshot(rec),
        reason=f"Updated via {change_source}",
    )
    return rec


async def forget_direct_memory(
    session: AsyncSession,
    *,
    native_user_id: str,
    memory_id: uuid.UUID | None = None,
    key: str | None = None,
    reason: str | None = None,
    change_source: str = "chat_tool",
) -> list[MemoryRecord]:
    """Archive one or more active memories by ID or key."""
    user_stmt = select(UserIdentity.id).where(UserIdentity.native_user_id == native_user_id)
    user_id = (await session.execute(user_stmt)).scalar_one_or_none()
    if user_id is None:
        return []

    if memory_id is None and (key is None or not key.strip()):
        return []

    stmt = (
        select(MemoryRecord)
        .where(
            MemoryRecord.user_id == user_id,
            MemoryRecord.state == "active",
        )
        .with_for_update()
    )

    if memory_id is not None:
        stmt = stmt.where(MemoryRecord.id == memory_id)
    elif key:
        stmt = stmt.where(MemoryRecord.key == key.strip().lower())

    records = list((await session.execute(stmt)).scalars().all())
    for rec in records:
        prev_snap = _memory_snapshot(rec)
        rec.state = "archived"
        rec.archived_at = func.now()
        await log_memory_change(
            session,
            native_user_id=native_user_id,
            user_id=user_id,
            memory_id=rec.id,
            change_source=change_source,
            action="archive",
            previous_state=prev_snap,
            new_state=_memory_snapshot(rec),
            reason=reason or f"Archived via {change_source}",
        )

    await session.flush()
    return records


async def revert_consolidation_item(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    item_index: int,
) -> dict[str, Any]:
    """Revert a single decision item within a consolidation run."""
    run_stmt = select(ConsolidationRun).where(ConsolidationRun.id == run_id).with_for_update()
    run = (await session.execute(run_stmt)).scalar_one_or_none()
    if run is None:
        raise ValueError("Consolidation run not found")

    if item_index < 0 or item_index >= len(run.details):
        raise ValueError("Resolution item index out of bounds")

    item = dict(run.details[item_index])
    if item.get("reverted"):
        raise ValueError("Resolution item has already been reverted")

    item_type = item.get("type")
    reverted_memory_id: uuid.UUID | None = None

    if item_type == "supersession":
        superseded_id = uuid.UUID(item["superseded_id"])
        reverted_memory_id = superseded_id
        rec_stmt = select(MemoryRecord).where(MemoryRecord.id == superseded_id).with_for_update()
        rec = (await session.execute(rec_stmt)).scalar_one_or_none()
        if rec is not None:
            prev_snap = _memory_snapshot(rec)
            rec.state = "active"
            rec.superseded_at = None
            rec.superseded_by_id = None
            await log_memory_change(
                session,
                native_user_id=run.native_user_id,
                user_id=run.user_id,
                memory_id=rec.id,
                change_source="admin_ui",
                action="revert_supersession",
                previous_state=prev_snap,
                new_state=_memory_snapshot(rec),
                reason=f"Reverted consolidation supersession #{item_index + 1} from run {run.id}",
            )
    elif item_type == "validity_update":
        memory_id = uuid.UUID(item["memory_id"])
        reverted_memory_id = memory_id
        rec_stmt = select(MemoryRecord).where(MemoryRecord.id == memory_id).with_for_update()
        rec = (await session.execute(rec_stmt)).scalar_one_or_none()
        if rec is not None:
            prev_snap = _memory_snapshot(rec)
            old_val = item.get("old_validity")
            if old_val in ("permanent", "none", None):
                rec.expires_at = None
                rec.temporal_tag = None
                rec.state = "active"
            elif old_val == "expired":
                rec.state = "expired"
            else:
                try:
                    rec.expires_at = datetime.fromisoformat(old_val)
                    rec.state = "active"
                except ValueError:
                    rec.expires_at = None
                    rec.state = "active"
            await log_memory_change(
                session,
                native_user_id=run.native_user_id,
                user_id=run.user_id,
                memory_id=rec.id,
                change_source="admin_ui",
                action="revert_validity_update",
                previous_state=prev_snap,
                new_state=_memory_snapshot(rec),
                reason=f"Reverted consolidation validity update #{item_index + 1} from run {run.id}",
            )
    elif item_type == "reclassification":
        memory_id = uuid.UUID(item["memory_id"])
        reverted_memory_id = memory_id
        rec_stmt = select(MemoryRecord).where(MemoryRecord.id == memory_id).with_for_update()
        rec = (await session.execute(rec_stmt)).scalar_one_or_none()
        if rec is not None:
            prev_snap = _memory_snapshot(rec)
            rec.category = item.get("old_category", "fact")
            await log_memory_change(
                session,
                native_user_id=run.native_user_id,
                user_id=run.user_id,
                memory_id=rec.id,
                change_source="admin_ui",
                action="revert_reclassification",
                previous_state=prev_snap,
                new_state=_memory_snapshot(rec),
                reason=f"Reverted consolidation category reclassification #{item_index + 1} from run {run.id}",
            )
    elif item_type == "promotion":
        memory_id = uuid.UUID(item["memory_id"])
        reverted_memory_id = memory_id
        rec_stmt = select(MemoryRecord).where(MemoryRecord.id == memory_id).with_for_update()
        rec = (await session.execute(rec_stmt)).scalar_one_or_none()
        if rec is not None and rec.kind == "explicit":
            prev_snap = _memory_snapshot(rec)
            rec.kind = "inferred"
            rec.confidence = 0
            await log_memory_change(
                session,
                native_user_id=run.native_user_id,
                user_id=run.user_id,
                memory_id=rec.id,
                change_source="admin_ui",
                action="revert_promotion",
                previous_state=prev_snap,
                new_state=_memory_snapshot(rec),
                reason=f"Reverted inferred-memory promotion #{item_index + 1} from run {run.id}",
            )
    elif item_type == "discard":
        memory_id = uuid.UUID(item["memory_id"])
        reverted_memory_id = memory_id
        rec_stmt = select(MemoryRecord).where(MemoryRecord.id == memory_id).with_for_update()
        rec = (await session.execute(rec_stmt)).scalar_one_or_none()
        if rec is not None:
            prev_snap = _memory_snapshot(rec)
            rec.state = "active"
            rec.archived_at = None
            await log_memory_change(
                session,
                native_user_id=run.native_user_id,
                user_id=run.user_id,
                memory_id=rec.id,
                change_source="admin_ui",
                action="revert_discard",
                previous_state=prev_snap,
                new_state=_memory_snapshot(rec),
                reason=f"Reverted inferred-memory discard #{item_index + 1} from run {run.id}",
            )

    item["reverted"] = True
    item["reverted_at"] = datetime.now(UTC).isoformat()
    updated_details = list(run.details)
    updated_details[item_index] = item
    run.details = updated_details
    flag_modified(run, "details")
    await session.flush()
    return {"success": True, "item": item, "memory_id": str(reverted_memory_id)}


async def revert_memory_change_log(
    session: AsyncSession,
    *,
    log_id: uuid.UUID,
) -> dict[str, Any]:
    """Revert a memory change log entry, restoring previous state or archiving."""
    log_stmt = select(MemoryChangeLog).where(MemoryChangeLog.id == log_id).with_for_update()
    log_entry = (await session.execute(log_stmt)).scalar_one_or_none()
    if log_entry is None:
        raise ValueError("Change log entry not found")
    if log_entry.is_reverted:
        raise ValueError("Change log has already been reverted")
    if log_entry.memory_id is None:
        raise ValueError("Change log has no associated memory record")

    rec_stmt = select(MemoryRecord).where(MemoryRecord.id == log_entry.memory_id).with_for_update()
    rec = (await session.execute(rec_stmt)).scalar_one_or_none()

    if log_entry.action == "create":
        if rec is not None:
            rec.state = "archived"
            rec.archived_at = func.now()
    else:
        if rec is not None and log_entry.previous_state:
            prev = log_entry.previous_state
            rec.statement = prev.get("statement", rec.statement)
            rec.category = prev.get("category", rec.category)
            rec.state = prev.get("state", "active")
            rec.temporal_tag = prev.get("temporal_tag")
            exp_str = prev.get("expires_at")
            rec.expires_at = datetime.fromisoformat(exp_str) if exp_str else None
            if rec.state == "active":
                rec.superseded_at = None
                rec.superseded_by_id = None
                rec.archived_at = None

    log_entry.is_reverted = True
    log_entry.reverted_at = datetime.now(UTC)
    await session.flush()
    return {
        "success": True,
        "log_id": str(log_entry.id),
        "memory_id": str(log_entry.memory_id),
    }


async def list_memory_change_logs(
    session: AsyncSession,
    *,
    native_user_id: str | None = None,
    memory_id: uuid.UUID | None = None,
    change_source: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[MemoryChangeLog], int]:
    """List historical memory change logs with pagination."""
    query = select(MemoryChangeLog)
    count_query = select(func.count(MemoryChangeLog.id))

    if native_user_id and native_user_id.strip():
        query = query.where(MemoryChangeLog.native_user_id == native_user_id.strip())
        count_query = count_query.where(MemoryChangeLog.native_user_id == native_user_id.strip())

    if memory_id:
        query = query.where(MemoryChangeLog.memory_id == memory_id)
        count_query = count_query.where(MemoryChangeLog.memory_id == memory_id)

    if change_source and change_source.strip():
        query = query.where(MemoryChangeLog.change_source == change_source.strip())
        count_query = count_query.where(MemoryChangeLog.change_source == change_source.strip())

    total = (await session.execute(count_query)).scalar_one()
    offset = (max(1, page) - 1) * page_size
    items = list(
        (
            await session.execute(
                query.order_by(MemoryChangeLog.created_at.desc()).offset(offset).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return items, total
