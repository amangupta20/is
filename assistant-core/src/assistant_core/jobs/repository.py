"""PostgreSQL claim and lifecycle operations for durable jobs."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def complete_job(session: AsyncSession, job: Job) -> None:
    """Persist successful completion and clear any prior error code."""
    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.last_error_code = None
    await session.commit()


async def fail_job(session: AsyncSession, job: Job, error_code: str) -> None:
    """Persist one retryable failure with a bounded delay and error code."""
    job.attempts += 1
    job.status = "queued" if job.attempts < 8 else "dead"
    job.available_at = datetime.now(UTC) + timedelta(
        seconds=_retry_delay_seconds(job.attempts)
    )
    job.last_error_code = error_code[:120]
    await session.commit()
