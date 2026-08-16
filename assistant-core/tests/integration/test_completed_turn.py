"""Live PostgreSQL coverage for completed-turn worker materialization."""

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import anyio
import pytest
from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_core.db.session import create_database
from assistant_core.events.models import EventInbox
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.turns.models import CompletedTurn

TEST_DATABASE_URL_ENV = "ASSISTANT_TEST_DATABASE_URL"


def configured_database_url() -> str:
    """Return only an explicitly configured live-test URL without exposing it."""
    database_url = os.getenv(TEST_DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is required for live integration tests")
    return database_url


@asynccontextmanager
async def database_context() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a live session factory and always dispose its engine."""
    engine, session_factory = create_database(configured_database_url())
    try:
        yield session_factory
    finally:
        await engine.dispose()


def scope_claim_to_job(monkeypatch: pytest.MonkeyPatch, identity_key: str) -> None:
    """Constrain the production claim statement to this test's exact UUID job."""
    from assistant_core.jobs import repository

    real_claim_statement = repository._claim_statement

    def scoped_claim_statement(now: datetime) -> Select[tuple[Job]]:
        return real_claim_statement(now).where(Job.identity_key == identity_key)

    monkeypatch.setattr(repository, "_claim_statement", scoped_claim_statement)


async def cleanup_exact_rows(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    event_id: str,
    identity_key: str,
    native_user_id: str,
) -> None:
    """Delete only UUID-owned rows in reverse dependency order and prove absence."""
    async with session_factory() as session, session.begin():
        await session.execute(delete(CompletedTurn).where(CompletedTurn.event_id == event_id))
        await session.execute(delete(Job).where(Job.identity_key == identity_key))
        await session.execute(delete(EventInbox).where(EventInbox.event_id == event_id))
        await session.execute(
            delete(UserIdentity).where(UserIdentity.native_user_id == native_user_id)
        )

    async with session_factory() as session:
        remaining = (
            await session.scalar(
                select(func.count())
                .select_from(UserIdentity)
                .where(UserIdentity.native_user_id == native_user_id)
            )
            or 0
        )
        remaining += (
            await session.scalar(
                select(func.count()).select_from(EventInbox).where(EventInbox.event_id == event_id)
            )
            or 0
        )
        remaining += (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.identity_key == identity_key)
            )
            or 0
        )
        remaining += (
            await session.scalar(
                select(func.count())
                .select_from(CompletedTurn)
                .where(CompletedTurn.event_id == event_id)
            )
            or 0
        )
    if remaining != 0:
        pytest.fail("exact completed-turn integration cleanup was incomplete")


async def completed_turn_table_exists(
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """Check migration readiness without writing or exposing connection details."""
    async with session_factory() as session:
        ready = await session.scalar(
            text("SELECT to_regclass('assistant_core.completed_turn') IS NOT NULL")
        )
    return bool(ready)


async def exercise_completed_turn_worker(monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    """Materialize through the worker, then prove repository replay idempotency."""
    from assistant_core.jobs.worker import process_one
    from assistant_core.turns.repository import materialize_completed_turn

    event_id = f"codex-test-turn-event-{suffix}"
    identity_key = f"event:{event_id}"
    native_user_id = f"codex-test-turn-user-{suffix}"
    user_content = "integration user content"
    assistant_content = "integration assistant content"
    user_digest = sha256(user_content.encode("utf-8")).hexdigest()
    assistant_digest = sha256(assistant_content.encode("utf-8")).hexdigest()
    scope_claim_to_job(monkeypatch, identity_key)

    async with database_context() as session_factory:
        if not await completed_turn_table_exists(session_factory):
            pytest.skip("completed-turn migration is required for live integration")
        try:
            async with session_factory() as session, session.begin():
                identity = UserIdentity(native_user_id=native_user_id)
                session.add(identity)
                await session.flush()
                session.add(
                    EventInbox(
                        event_id=event_id,
                        event_type="turn.completed.v1",
                        user_id=identity.id,
                        native_chat_id=f"chat-{suffix}",
                        native_message_id=f"assistant-{suffix}",
                        occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
                        payload={
                            "source": "openwebui_outlet_filter",
                            "user_message": {
                                "id": f"user-{suffix}",
                                "role": "user",
                                "content": user_content,
                                "sha256": user_digest,
                            },
                            "assistant_message": {
                                "id": f"assistant-{suffix}",
                                "role": "assistant",
                                "content": assistant_content,
                                "sha256": assistant_digest,
                            },
                        },
                    )
                )
                session.add(
                    Job(
                        identity_key=identity_key,
                        kind="process_event",
                        status="queued",
                        payload={"event_id": event_id},
                        attempts=0,
                        available_at=datetime.now(UTC) - timedelta(seconds=1),
                    )
                )

            async with session_factory() as session:
                processed = await process_one(session)
            if not processed:
                pytest.fail("the exact completed-turn job was not processed")

            async with session_factory() as session:
                materialized_count = await session.scalar(
                    select(func.count())
                    .select_from(CompletedTurn)
                    .where(CompletedTurn.event_id == event_id)
                )
                job_status = await session.scalar(
                    select(Job.status).where(Job.identity_key == identity_key)
                )
                event = await session.get(EventInbox, event_id)
                if event is None:
                    pytest.fail("the exact inbox event disappeared")
                replay_inserted = await materialize_completed_turn(session, event)
                await session.commit()
                replay_count = await session.scalar(
                    select(func.count())
                    .select_from(CompletedTurn)
                    .where(CompletedTurn.event_id == event_id)
                )

            if materialized_count != 1 or replay_count != 1:
                pytest.fail("completed-turn materialization was not exactly-once")
            if job_status != "completed":
                pytest.fail("completed-turn worker job did not complete")
            if replay_inserted:
                pytest.fail("completed-turn replay was reported as a new insert")
        finally:
            await cleanup_exact_rows(
                session_factory,
                event_id=event_id,
                identity_key=identity_key,
                native_user_id=native_user_id,
            )


def test_live_worker_materializes_completed_turn_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One UUID-owned event becomes one row and remains one on replay."""
    anyio.run(exercise_completed_turn_worker, monkeypatch, uuid.uuid4().hex)
