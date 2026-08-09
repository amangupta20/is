"""Focused unit tests for the foundation job worker."""

import asyncio
import uuid
from datetime import UTC, datetime

import anyio
import pytest
from sqlalchemy.dialects import postgresql

from assistant_core.events.models import EventInbox
from assistant_core.jobs.models import Job


def inbox_event(event_type: str = "chat.created") -> EventInbox:
    """Build one event available to a worker unit test."""
    return EventInbox(
        event_id="exact-event-id",
        event_type=event_type,
        user_id=uuid.uuid4(),
        native_chat_id="chat-1",
        native_message_id="assistant-1",
        occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
        payload={"source": "metadata-only"},
    )


@pytest.mark.parametrize("event_type", ["chat.created", "turn.oversized.v1"])
def test_metadata_only_events_are_successful_noops(
    monkeypatch: pytest.MonkeyPatch, event_type: str
) -> None:
    """Lifecycle and oversized markers remain metadata-only in Phase 2A."""
    from assistant_core.jobs import worker

    event = inbox_event(event_type)

    class Session:
        async def get(self, model: object, key: object) -> EventInbox:
            assert model is EventInbox
            assert key == "exact-event-id"
            return event

    async def unexpected(*_args: object) -> bool:
        pytest.fail("metadata-only event was materialized")

    monkeypatch.setattr(worker, "materialize_completed_turn", unexpected)
    anyio.run(worker.handle, Session(), "process_event", {"event_id": "exact-event-id"})  # type: ignore[arg-type]


def test_completed_event_loads_exact_inbox_row_and_materializes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The process-event payload selects the exact inbox event for routing."""
    from assistant_core.jobs import worker

    event = inbox_event("turn.completed.v1")
    calls: list[EventInbox] = []

    class Session:
        async def get(self, model: object, key: object) -> EventInbox:
            assert model is EventInbox
            assert key == "exact-event-id"
            return event

    async def materialize(session: object, selected: EventInbox) -> bool:
        assert isinstance(session, Session)
        calls.append(selected)
        return True

    monkeypatch.setattr(worker, "materialize_completed_turn", materialize)
    anyio.run(worker.handle, Session(), "process_event", {"event_id": "exact-event-id"})  # type: ignore[arg-type]
    assert calls == [event]


def test_unsupported_kind_raises_constant_payload_free_error() -> None:
    """Unsupported jobs fail without rendering their payload."""
    from assistant_core.jobs.worker import (
        UNSUPPORTED_KIND_ERROR,
        UnsupportedJobKindError,
        handle,
    )

    secret_marker = "must-not-appear"

    async def exercise() -> None:
        with pytest.raises(UnsupportedJobKindError) as captured:
            await handle(  # type: ignore[arg-type]
                object(), "unsupported", {"secret": secret_marker}
            )

        assert str(captured.value) == UNSUPPORTED_KIND_ERROR
        assert secret_marker not in str(captured.value)

    anyio.run(exercise)


def test_claim_statement_uses_postgres_skip_locked() -> None:
    """The claim query explicitly skips rows locked by another worker."""
    from assistant_core.jobs.repository import _claim_statement

    statement = _claim_statement(datetime(2026, 8, 9, tzinfo=UTC))
    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE SKIP LOCKED" in compiled


def test_claim_statement_orders_oldest_first_and_limits_one() -> None:
    """The claim query selects at most the oldest eligible row."""
    from assistant_core.jobs.repository import _claim_statement

    statement = _claim_statement(datetime(2026, 8, 9, tzinfo=UTC))
    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "ORDER BY assistant_core.job.available_at, assistant_core.job.id" in compiled
    assert "LIMIT" in compiled


@pytest.mark.parametrize(("attempts", "expected"), [(1, 2), (7, 128), (8, 256), (20, 256)])
def test_retry_delay_is_exponential_and_capped(attempts: int, expected: int) -> None:
    """Retry delay follows the bounded formula for persisted attempt counts."""
    from assistant_core.jobs.repository import _retry_delay_seconds

    assert _retry_delay_seconds(attempts) == expected


@pytest.mark.parametrize("signal_type", [asyncio.CancelledError, SystemExit])
def test_process_one_does_not_swallow_exit_signals(
    monkeypatch: pytest.MonkeyPatch, signal_type: type[BaseException]
) -> None:
    """Cancellation and system exit escape instead of becoming job failures."""
    from assistant_core.jobs import worker

    job = Job(identity_key="unit-test", kind="process_event", payload={})

    async def claim(_session: object) -> Job:
        return job

    async def stop(_session: object, _kind: str, _payload: object) -> None:
        raise signal_type()

    async def unexpected(*_args: object) -> None:
        pytest.fail("exit signal was converted into a lifecycle write")

    monkeypatch.setattr(worker, "claim_next_job", claim)
    monkeypatch.setattr(worker, "handle", stop)
    monkeypatch.setattr(worker, "complete_job", unexpected)

    async def exercise() -> None:
        with pytest.raises(signal_type):
            await worker.process_one(object())  # type: ignore[arg-type]

    anyio.run(exercise)


def test_invalid_completed_turn_uses_fixed_safe_failure_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid content cannot become a persisted worker error string."""
    from assistant_core.jobs import worker
    from assistant_core.turns.repository import InvalidTurnPayloadError

    job = Job(identity_key="unit-invalid", kind="process_event", payload={"event_id": "e"})
    failures: list[str] = []

    async def claim(_session: object) -> Job:
        return job

    async def reject(*_args: object) -> None:
        raise InvalidTurnPayloadError("invalid_turn_payload")

    async def fail(_session: object, _job: Job, code: str) -> None:
        failures.append(code)

    async def unexpected(*_args: object) -> None:
        pytest.fail("invalid turn was completed")

    monkeypatch.setattr(worker, "claim_next_job", claim)
    monkeypatch.setattr(worker, "handle", reject)
    monkeypatch.setattr(worker, "fail_job", fail)
    monkeypatch.setattr(worker, "complete_job", unexpected)

    anyio.run(worker.process_one, object())  # type: ignore[arg-type]
    assert failures == ["invalid_turn_payload"]


def test_worker_builds_database_from_settings_and_always_disposes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even an already-stopped worker constructs and disposes its engine."""
    from assistant_core.config import Settings
    from assistant_core.jobs import worker

    database_url = "postgresql://worker:worker@localhost/worker"
    created_urls: list[str] = []

    class Engine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engine = Engine()

    def create_database(url: str) -> tuple[Engine, object]:
        created_urls.append(url)
        return engine, object()

    monkeypatch.setattr(worker, "get_settings", lambda: Settings(database_url=database_url))
    monkeypatch.setattr(worker, "create_database", create_database, raising=False)

    async def exercise() -> None:
        stop_event = asyncio.Event()
        stop_event.set()
        await worker.run_worker(stop_event)

    anyio.run(exercise)

    assert created_urls == [database_url]
    assert engine.disposed is True


def test_worker_idle_poll_cadence_is_one_second() -> None:
    """No-work polling uses the required one-second idle cadence."""
    from assistant_core.jobs.worker import IDLE_POLL_SECONDS

    assert IDLE_POLL_SECONDS == 1.0


def test_main_runs_worker_coroutine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The executable entry point submits the worker to asyncio."""
    from collections.abc import Coroutine
    from typing import Any

    from assistant_core.jobs import worker

    submitted: list[Coroutine[Any, Any, None]] = []

    def run(coroutine: Coroutine[Any, Any, None]) -> None:
        submitted.append(coroutine)
        coroutine.close()

    monkeypatch.setattr(worker.asyncio, "run", run)

    worker.main()

    assert len(submitted) == 1
