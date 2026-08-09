"""Live PostgreSQL tests for the durable worker lifecycle."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from sqlalchemy import Select, and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_core.db.session import create_database
from assistant_core.jobs.models import Job

TEST_DATABASE_URL_ENV = "ASSISTANT_TEST_DATABASE_URL"
TEST_IDENTITY_PREFIX = "codex-test-worker-"


def configured_database_url() -> str:
    """Return the explicitly configured live test URL without exposing it."""
    database_url = os.getenv(TEST_DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is required for live integration tests")
    return database_url


def unique_identity() -> str:
    """Create an exact UUID-backed identity owned by one live test."""
    return f"{TEST_IDENTITY_PREFIX}{uuid.uuid4()}"


@asynccontextmanager
async def database_context() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a live session factory and always dispose its engine."""
    engine, session_factory = create_database(configured_database_url())
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def insert_job(
    session_factory: async_sessionmaker[AsyncSession],
    identity_key: str,
    *,
    kind: str = "process_event",
    status: str = "queued",
    attempts: int = 0,
    available_at: datetime | None = None,
    claimed_at: datetime | None = None,
    last_error_code: str | None = None,
) -> uuid.UUID:
    """Insert one exact test-owned job row."""
    async with session_factory() as session, session.begin():
        job = Job(
            identity_key=identity_key,
            kind=kind,
            status=status,
            payload={"source": "worker-integration"},
            attempts=attempts,
            available_at=available_at or datetime.now(UTC),
            claimed_at=claimed_at,
            last_error_code=last_error_code,
        )
        session.add(job)
        await session.flush()
        return job.id


async def read_job(
    session_factory: async_sessionmaker[AsyncSession], identity_key: str
) -> Job:
    """Read one exact test-owned job row."""
    async with session_factory() as session:
        return (
            await session.execute(select(Job).where(Job.identity_key == identity_key))
        ).scalar_one()


async def cleanup_exact_jobs(
    session_factory: async_sessionmaker[AsyncSession], identity_keys: Collection[str]
) -> None:
    """Delete only rows owned by this test and prove each one is gone."""
    async with session_factory() as session, session.begin():
        await session.execute(delete(Job).where(Job.identity_key.in_(identity_keys)))

    async with session_factory() as session:
        remaining = await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.identity_key.in_(identity_keys))
        )
    assert int(remaining or 0) == 0


async def refuse_if_other_eligible_job_exists(
    session_factory: async_sessionmaker[AsyncSession], allowed_identity_keys: Collection[str]
) -> None:
    """Skip on existing foreign work as defense in depth before a scoped claim."""
    now = datetime.now(UTC)
    eligible = or_(
        and_(Job.status == "queued", Job.available_at <= now),
        and_(Job.status == "running", Job.claimed_at <= now - timedelta(minutes=5)),
    )
    async with session_factory() as session:
        other_count = await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(eligible, Job.identity_key.not_in(allowed_identity_keys))
        )
    if int(other_count or 0):
        pytest.skip("eligible jobs outside this test's exact identities exist")


def scope_claims_to_identities(
    monkeypatch: pytest.MonkeyPatch, identity_keys: Collection[str]
) -> None:
    """Constrain the real production claim statement to exact test-owned rows."""
    from assistant_core.jobs import repository

    real_claim_statement = repository._claim_statement
    exact_identity_keys = tuple(identity_keys)

    def scoped_claim_statement(now: datetime) -> Select[tuple[Job]]:
        return real_claim_statement(now).where(
            Job.identity_key.in_(exact_identity_keys)
        )

    monkeypatch.setattr(repository, "_claim_statement", scoped_claim_statement)


async def exercise_concurrent_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove SKIP LOCKED prevents two workers from claiming one row."""
    from assistant_core.jobs.repository import claim_next_job

    identity_key = unique_identity()
    scope_claims_to_identities(monkeypatch, {identity_key})
    async with database_context() as session_factory:
        try:
            await insert_job(
                session_factory,
                identity_key,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await refuse_if_other_eligible_job_exists(session_factory, {identity_key})

            async with session_factory() as first, session_factory() as second:
                claims = await asyncio.gather(
                    claim_next_job(first),
                    claim_next_job(second),
                )

            claimed = [job for job in claims if job is not None]
            assert len(claimed) == 1
            assert claimed[0].identity_key == identity_key
            assert claimed[0].status == "running"
            assert claimed[0].claimed_at is not None
            assert claimed[0].claimed_at.tzinfo is not None
        finally:
            await cleanup_exact_jobs(session_factory, {identity_key})


def test_two_sessions_cannot_claim_one_eligible_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only one concurrent session receives the sole eligible job."""
    anyio.run(exercise_concurrent_claim, monkeypatch)


async def exercise_claim_scope_excludes_earlier_foreign_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an earlier eligible row outside one test claim's exact scope."""
    from assistant_core.jobs.repository import claim_next_job

    target_identity = unique_identity()
    foreign_identity = unique_identity()
    identities = {target_identity, foreign_identity}
    scope_claims_to_identities(monkeypatch, {target_identity})
    async with database_context() as session_factory:
        try:
            await insert_job(
                session_factory,
                target_identity,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await insert_job(
                session_factory,
                foreign_identity,
                available_at=datetime.now(UTC) - timedelta(minutes=10),
            )
            await refuse_if_other_eligible_job_exists(session_factory, identities)

            async with session_factory() as session:
                claimed = await claim_next_job(session)

            assert claimed is not None
            assert claimed.identity_key == target_identity
            foreign = await read_job(session_factory, foreign_identity)
            assert foreign.status == "queued"
            assert foreign.claimed_at is None
        finally:
            await cleanup_exact_jobs(session_factory, identities)


def test_claim_scope_excludes_earlier_eligible_foreign_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executed claim SQL cannot select outside the test's exact identity."""
    anyio.run(exercise_claim_scope_excludes_earlier_foreign_job, monkeypatch)


async def exercise_completion() -> None:
    """Complete one running row and verify its exact lifecycle state."""
    from assistant_core.jobs.repository import complete_job

    identity_key = unique_identity()
    async with database_context() as session_factory:
        try:
            await insert_job(
                session_factory,
                identity_key,
                status="running",
                attempts=3,
                claimed_at=datetime.now(UTC),
                last_error_code="prior_failure",
            )
            started_at = datetime.now(UTC)

            async with session_factory() as session:
                job = (
                    await session.execute(
                        select(Job).where(Job.identity_key == identity_key)
                    )
                ).scalar_one()
                await complete_job(session, job)

            completed = await read_job(session_factory, identity_key)
            assert completed.status == "completed"
            assert completed.attempts == 3
            assert completed.completed_at is not None
            assert completed.completed_at.tzinfo is not None
            assert completed.completed_at >= started_at
            assert completed.last_error_code is None
        finally:
            await cleanup_exact_jobs(session_factory, {identity_key})


def test_completion_persists_exact_completed_state() -> None:
    """Completion commits its timestamp and clears only the prior error code."""
    anyio.run(exercise_completion)


async def exercise_requeue_after_failure() -> None:
    """Fail one running row and verify its bounded retry state."""
    from assistant_core.jobs.repository import fail_job

    identity_key = unique_identity()
    error_code = "e" * 140
    async with database_context() as session_factory:
        try:
            await insert_job(
                session_factory,
                identity_key,
                status="running",
                claimed_at=datetime.now(UTC),
            )
            before_failure = datetime.now(UTC)

            async with session_factory() as session:
                job = (
                    await session.execute(
                        select(Job).where(Job.identity_key == identity_key)
                    )
                ).scalar_one()
                await fail_job(session, job, error_code)

            failed = await read_job(session_factory, identity_key)
            assert failed.status == "queued"
            assert failed.attempts == 1
            assert failed.available_at > before_failure
            assert failed.available_at <= datetime.now(UTC) + timedelta(seconds=300)
            assert failed.last_error_code == error_code[:120]
            assert len(failed.last_error_code) == 120
        finally:
            await cleanup_exact_jobs(session_factory, {identity_key})


def test_failure_increments_attempts_and_requeues_with_bounded_values() -> None:
    """A retryable failure commits one increment, delay, and bounded code."""
    anyio.run(exercise_requeue_after_failure)


async def exercise_eighth_failure() -> None:
    """Fail an attempt-seven row and verify it becomes dead."""
    from assistant_core.jobs.repository import fail_job

    identity_key = unique_identity()
    async with database_context() as session_factory:
        try:
            await insert_job(
                session_factory,
                identity_key,
                status="running",
                attempts=7,
                claimed_at=datetime.now(UTC),
            )

            async with session_factory() as session:
                job = (
                    await session.execute(
                        select(Job).where(Job.identity_key == identity_key)
                    )
                ).scalar_one()
                await fail_job(session, job, "handler_failed")

            failed = await read_job(session_factory, identity_key)
            assert failed.status == "dead"
            assert failed.attempts == 8
            assert failed.last_error_code == "handler_failed"
        finally:
            await cleanup_exact_jobs(session_factory, {identity_key})


def test_eighth_attempt_becomes_dead() -> None:
    """The eighth persisted failure transitions the job to dead."""
    anyio.run(exercise_eighth_failure)


async def exercise_stale_claim_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claim a running row whose lease is older than five minutes."""
    from assistant_core.jobs.repository import claim_next_job

    identity_key = unique_identity()
    stale_claimed_at = datetime.now(UTC) - timedelta(minutes=6)
    scope_claims_to_identities(monkeypatch, {identity_key})
    async with database_context() as session_factory:
        try:
            await insert_job(
                session_factory,
                identity_key,
                status="running",
                available_at=datetime.now(UTC) - timedelta(minutes=10),
                claimed_at=stale_claimed_at,
            )
            await refuse_if_other_eligible_job_exists(session_factory, {identity_key})

            async with session_factory() as session:
                reclaimed = await claim_next_job(session)

            assert reclaimed is not None
            assert reclaimed.identity_key == identity_key
            assert reclaimed.status == "running"
            assert reclaimed.claimed_at is not None
            assert reclaimed.claimed_at > stale_claimed_at
            assert reclaimed.claimed_at.tzinfo is not None
        finally:
            await cleanup_exact_jobs(session_factory, {identity_key})


def test_stale_running_job_can_be_reclaimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running job claimed over five minutes ago is eligible again."""
    anyio.run(exercise_stale_claim_recovery, monkeypatch)


async def exercise_ineligible_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify future queued and fresh running rows stay untouched."""
    from assistant_core.jobs.repository import claim_next_job

    future_identity = unique_identity()
    fresh_identity = unique_identity()
    identities = {future_identity, fresh_identity}
    scope_claims_to_identities(monkeypatch, identities)
    async with database_context() as session_factory:
        try:
            await insert_job(
                session_factory,
                future_identity,
                available_at=datetime.now(UTC) + timedelta(minutes=10),
            )
            await insert_job(
                session_factory,
                fresh_identity,
                status="running",
                available_at=datetime.now(UTC) - timedelta(minutes=10),
                claimed_at=datetime.now(UTC),
            )
            await refuse_if_other_eligible_job_exists(session_factory, identities)

            async with session_factory() as session:
                claimed = await claim_next_job(session)

            assert claimed is None
            future = await read_job(session_factory, future_identity)
            fresh = await read_job(session_factory, fresh_identity)
            assert future.status == "queued"
            assert future.claimed_at is None
            assert fresh.status == "running"
        finally:
            await cleanup_exact_jobs(session_factory, identities)


def test_future_queued_and_fresh_running_jobs_are_not_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Jobs outside both eligibility windows are not claimed."""
    anyio.run(exercise_ineligible_jobs, monkeypatch)


async def exercise_worker_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Process one foundation job through claim, handle, and completion."""
    from assistant_core.jobs.worker import process_one

    identity_key = unique_identity()
    scope_claims_to_identities(monkeypatch, {identity_key})
    async with database_context() as session_factory:
        try:
            await insert_job(
                session_factory,
                identity_key,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await refuse_if_other_eligible_job_exists(session_factory, {identity_key})

            async with session_factory() as session:
                processed = await process_one(session)

            completed = await read_job(session_factory, identity_key)
            assert processed is True
            assert completed.status == "completed"
            assert completed.completed_at is not None
            assert completed.last_error_code is None
        finally:
            await cleanup_exact_jobs(session_factory, {identity_key})


def test_worker_processes_foundation_job_to_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker completes one no-op process_event job."""
    anyio.run(exercise_worker_success, monkeypatch)


async def exercise_worker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Process one unsupported job through the bounded failure path."""
    from assistant_core.jobs.worker import process_one

    identity_key = unique_identity()
    scope_claims_to_identities(monkeypatch, {identity_key})
    async with database_context() as session_factory:
        try:
            await insert_job(
                session_factory,
                identity_key,
                kind="unsupported",
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await refuse_if_other_eligible_job_exists(session_factory, {identity_key})

            async with session_factory() as session:
                processed = await process_one(session)

            failed = await read_job(session_factory, identity_key)
            assert processed is True
            assert failed.status == "queued"
            assert failed.attempts == 1
            assert failed.last_error_code == "handler_failed"
        finally:
            await cleanup_exact_jobs(session_factory, {identity_key})


def test_worker_converts_ordinary_handler_failure_to_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary handler error is persisted only as handler_failed."""
    anyio.run(exercise_worker_failure, monkeypatch)
