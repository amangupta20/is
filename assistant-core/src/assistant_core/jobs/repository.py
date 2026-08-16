"""PostgreSQL claim and lifecycle operations for durable jobs."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from assistant_core.jobs.models import Job

STALE_CLAIM_AGE = timedelta(minutes=5)


def _retry_delay_seconds(attempts: int) -> int:
    """Calculate bounded exponential retry delay from the persisted attempt count."""
    return min(300, 1 << min(attempts, 8))


def _claim_statement(now: datetime) -> Select[tuple[Job]]:
    """Build the single-row PostgreSQL SKIP LOCKED claim statement."""
    return (
        select(Job)
        .where(
            or_(
                and_(Job.status == "queued", Job.available_at <= now),
                and_(
                    Job.status == "running",
                    Job.claimed_at <= now - STALE_CLAIM_AGE,
                ),
            )
        )
        .order_by(Job.available_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _complete_statement(job_id: uuid.UUID, expected_claimed_at: datetime, now: datetime) -> Update:
    """Build a completion update owned by one exact active claim."""
    return (
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "running",
            Job.claimed_at == expected_claimed_at,
        )
        .values(status="completed", completed_at=now, last_error_code=None)
        .execution_options(synchronize_session=False)
    )


def _failure_statement(
    job_id: uuid.UUID,
    expected_claimed_at: datetime,
    attempts: int,
    now: datetime,
    error_code: str,
) -> Update:
    """Build a retry/dead update owned by one exact active claim."""
    next_attempts = attempts + 1
    return (
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "running",
            Job.claimed_at == expected_claimed_at,
        )
        .values(
            attempts=next_attempts,
            status="queued" if next_attempts < 8 else "dead",
            available_at=now + timedelta(seconds=_retry_delay_seconds(next_attempts)),
            last_error_code=error_code[:120],
        )
        .execution_options(synchronize_session=False)
    )


async def _commit_owned_update(session: AsyncSession, statement: Update) -> bool:
    """Commit one lease-owned update or roll back all work after claim loss."""
    result = cast(CursorResult[Any], await session.execute(statement))
    if result.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    return True


async def claim_next_job(session: AsyncSession) -> Job | None:
    """Durably claim the oldest eligible job, skipping rows locked by peers."""
    now = datetime.now(UTC)
    job = (await session.execute(_claim_statement(now))).scalar_one_or_none()
    if job is None:
        return None

    job.status = "running"
    job.claimed_at = datetime.now(UTC)
    await session.commit()
    return job


async def complete_job(
    session: AsyncSession, job_id: uuid.UUID, expected_claimed_at: datetime
) -> bool:
    """Complete a job only while its exact claim remains active."""
    return await _commit_owned_update(
        session,
        _complete_statement(job_id, expected_claimed_at, datetime.now(UTC)),
    )


async def fail_job(
    session: AsyncSession,
    job: Job,
    expected_claimed_at: datetime,
    error_code: str,
) -> bool:
    """Persist failure state only while the exact claim remains active."""
    return await _commit_owned_update(
        session,
        _failure_statement(
            job.id,
            expected_claimed_at,
            job.attempts,
            datetime.now(UTC),
            error_code,
        ),
    )
