"""Focused unit tests for the foundation job worker."""

import asyncio
import uuid
from datetime import UTC, datetime
from hashlib import sha256

import anyio
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, PendingRollbackError

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


def completed_inbox_event(content: str) -> EventInbox:
    """Build one strictly valid completed-turn event containing exact content."""
    user_content = "worker failure user content"
    return EventInbox(
        event_id="exact-completed-event",
        event_type="turn.completed.v1",
        user_id=uuid.uuid4(),
        native_chat_id="exact-completed-chat",
        native_message_id="exact-assistant-message",
        occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
        payload={
            "source": "openwebui_outlet_filter",
            "user_message": {
                "id": "exact-user-message",
                "role": "user",
                "content": user_content,
                "sha256": sha256(user_content.encode()).hexdigest(),
            },
            "assistant_message": {
                "id": "exact-assistant-message",
                "role": "assistant",
                "content": content,
                "sha256": sha256(content.encode()).hexdigest(),
            },
        },
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
    from assistant_core.turns.models import CompletedTurn

    event = inbox_event("turn.completed.v1")
    turn = CompletedTurn(
        id=uuid.uuid4(),
        event_id=event.event_id,
        user_id=event.user_id,
        native_chat_id=event.native_chat_id,
        native_user_message_id="user-1",
        native_assistant_message_id="assistant-1",
        user_content="content",
        assistant_content="response",
        user_content_sha256="a" * 64,
        assistant_content_sha256="b" * 64,
        occurred_at=event.occurred_at,
    )
    calls: list[EventInbox] = []

    class Session:
        async def get(self, model: object, key: object) -> EventInbox:
            assert model is EventInbox
            assert key == "exact-event-id"
            return event

        async def execute(self, _statement: object) -> object:
            class Result:
                rowcount = 1

            return Result()

    async def materialize(session: object, selected: EventInbox) -> bool:
        assert isinstance(session, Session)
        calls.append(selected)
        return True

    monkeypatch.setattr(worker, "materialize_completed_turn", materialize)
    async def get_turn(*_args: object) -> CompletedTurn:
        return turn

    monkeypatch.setattr(worker, "get_completed_turn_for_event", get_turn)
    anyio.run(worker.handle, Session(), "process_event", {"event_id": "exact-event-id"})  # type: ignore[arg-type]
    assert calls == [event]


def test_completed_turn_enqueue_commits_before_later_extractor_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider failure cannot roll back the persisted turn or extraction job."""
    from assistant_core.jobs import worker
    from assistant_core.turns.models import CompletedTurn

    event = completed_inbox_event("assistant content")
    turn = CompletedTurn(
        id=uuid.uuid4(),
        event_id=event.event_id,
        user_id=event.user_id,
        native_chat_id=event.native_chat_id,
        native_user_message_id="exact-user-message",
        native_assistant_message_id="exact-assistant-message",
        user_content="worker failure user content",
        assistant_content="assistant content",
        user_content_sha256="a" * 64,
        assistant_content_sha256="b" * 64,
        occurred_at=event.occurred_at,
    )
    process_job = Job(
        id=uuid.uuid4(),
        identity_key="event:exact-completed-event",
        kind="process_event",
        status="running",
        payload={"event_id": event.event_id},
        claimed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    extract_job = Job(
        id=uuid.uuid4(),
        identity_key=f"memory:{turn.id}",
        kind="extract_memory",
        status="running",
        payload={"turn_id": str(turn.id)},
        claimed_at=datetime(2026, 8, 10, 0, 1, tzinfo=UTC),
    )
    commits: list[str] = []
    failures: list[str] = []

    class Session:
        async def get(self, model: object, key: object) -> object:
            if model is EventInbox:
                assert key == event.event_id
                return event
            if model is CompletedTurn:
                assert key == turn.id
                return turn
            if model is Job:
                return extract_job
            pytest.fail("unexpected model load")

        async def execute(self, _statement: object) -> object:
            class Result:
                def scalar_one_or_none(self) -> object:
                    return turn

                rowcount = 1

            return Result()

        async def commit(self) -> None:
            commits.append("commit")

        async def rollback(self) -> None:
            pass

    session = Session()
    claimed = iter([process_job, extract_job])

    async def claim(_session: object) -> Job:
        return next(claimed)

    async def materialize(_session: object, selected: EventInbox) -> bool:
        assert selected is event
        return True

    async def get_turn(_session: object, event_id: str) -> CompletedTurn:
        assert event_id == event.event_id
        return turn

    class FailingExtractor:
        def extract(self, _turn: object) -> list[object]:
            from assistant_core.memory.extractor import (
                MEMORY_EXTRACTION_FAILED_ERROR,
                MemoryExtractionError,
            )

            raise MemoryExtractionError(MEMORY_EXTRACTION_FAILED_ERROR)

    async def fail(
        _session: object, _job: Job, _lease: datetime, code: str
    ) -> bool:
        failures.append(code)
        return True

    monkeypatch.setattr(worker, "claim_next_job", claim)
    monkeypatch.setattr(worker, "materialize_completed_turn", materialize)
    monkeypatch.setattr(worker, "get_completed_turn_for_event", get_turn)
    monkeypatch.setattr(worker, "get_memory_extractor", lambda: FailingExtractor())
    monkeypatch.setattr(worker, "fail_job", fail)

    assert anyio.run(worker.process_one, session) is True  # type: ignore[arg-type]
    assert anyio.run(worker.process_one, session) is True  # type: ignore[arg-type]
    assert commits == ["commit"]
    assert failures == ["memory_extraction_failed"]


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


def test_completion_and_failure_updates_require_exact_active_claim() -> None:
    """Lifecycle SQL guards ownership by ID, running state, and lease timestamp."""
    from assistant_core.jobs.repository import _complete_statement, _failure_statement

    job_id = uuid.uuid4()
    lease = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    now = datetime(2026, 8, 10, 12, 1, tzinfo=UTC)
    complete = _complete_statement(job_id, lease, now)
    failure = _failure_statement(job_id, lease, 7, now, "handler_failed")

    for statement in (complete, failure):
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "assistant_core.job.id =" in sql
        assert "assistant_core.job.status =" in sql
        assert "assistant_core.job.claimed_at =" in sql
        assert job_id in compiled.params.values()
        assert lease in compiled.params.values()
        assert "running" in compiled.params.values()

    assert complete.compile().params["status"] == "completed"
    failure_params = failure.compile().params
    assert failure_params["attempts"] == 8
    assert failure_params["status"] == "dead"
    assert failure_params["last_error_code"] == "handler_failed"


def test_lost_claim_rolls_back_without_completion_or_failure_overwrite() -> None:
    """A zero-row lease guard is a safe no-op for both lifecycle outcomes."""
    from assistant_core.jobs.repository import complete_job, fail_job

    lease = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    job = Job(
        id=uuid.uuid4(),
        identity_key="unit-lost-lease",
        kind="process_event",
        status="running",
        payload={"event_id": "event-1"},
        attempts=7,
        claimed_at=datetime(2026, 8, 10, 12, 6, tzinfo=UTC),
    )

    class Session:
        rollback_count = 0
        commit_count = 0

        async def execute(self, _statement: object) -> object:
            class Result:
                rowcount = 0

            return Result()

        async def rollback(self) -> None:
            self.rollback_count += 1

        async def commit(self) -> None:
            self.commit_count += 1

    async def exercise() -> tuple[bool, bool, Session, Session]:
        completion_session = Session()
        failure_session = Session()
        completed = await complete_job(  # type: ignore[arg-type]
            completion_session, job, lease
        )
        failed = await fail_job(  # type: ignore[arg-type]
            failure_session, job, lease, "handler_failed"
        )
        return completed, failed, completion_session, failure_session

    completed, failed, completion_session, failure_session = anyio.run(exercise)

    assert completed is False
    assert failed is False
    assert completion_session.rollback_count == 1
    assert failure_session.rollback_count == 1
    assert completion_session.commit_count == 0
    assert failure_session.commit_count == 0


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

    job = Job(
        id=uuid.uuid4(),
        identity_key="unit-invalid",
        kind="process_event",
        payload={"event_id": "e"},
        claimed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    failures: list[str] = []

    class Session:
        async def rollback(self) -> None:
            pass

        async def get(self, model: object, key: object) -> Job:
            assert model is Job
            assert key == job.id
            return job

    session = Session()

    async def claim(_session: object) -> Job:
        return job

    async def reject(*_args: object) -> None:
        raise InvalidTurnPayloadError("invalid_turn_payload")

    async def fail(
        _session: object, _job: Job, _lease: datetime, code: str
    ) -> bool:
        failures.append(code)
        return True

    async def unexpected(*_args: object) -> None:
        pytest.fail("invalid turn was completed")

    monkeypatch.setattr(worker, "claim_next_job", claim)
    monkeypatch.setattr(worker, "handle", reject)
    monkeypatch.setattr(worker, "fail_job", fail)
    monkeypatch.setattr(worker, "complete_job", unexpected)

    anyio.run(worker.process_one, session)  # type: ignore[arg-type]
    assert failures == ["invalid_turn_payload"]


def test_process_one_captures_claim_before_successful_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler cannot replace the lease used by successful finalization."""
    from assistant_core.jobs import worker

    original_lease = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    replacement_lease = datetime(2026, 8, 10, 12, 6, tzinfo=UTC)
    job = Job(
        id=uuid.uuid4(),
        identity_key="unit-success-lease",
        kind="process_event",
        status="running",
        payload={"event_id": "event-1"},
        attempts=0,
        claimed_at=original_lease,
    )
    finalized_leases: list[datetime] = []

    async def claim(_session: object) -> Job:
        return job

    async def handle(_session: object, _kind: str, _payload: object) -> None:
        job.claimed_at = replacement_lease

    async def complete(
        _session: object, _job: Job, lease: datetime
    ) -> bool:
        finalized_leases.append(lease)
        return False

    monkeypatch.setattr(worker, "claim_next_job", claim)
    monkeypatch.setattr(worker, "handle", handle)
    monkeypatch.setattr(worker, "complete_job", complete)

    processed = anyio.run(worker.process_one, object())  # type: ignore[arg-type]

    assert processed is True
    assert finalized_leases == [original_lease]


def test_failure_recovery_keeps_original_claim_after_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reclaimed job cannot be failed by the older worker after rollback."""
    from assistant_core.jobs import worker

    original_lease = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    replacement_lease = datetime(2026, 8, 10, 12, 6, tzinfo=UTC)
    claimed_job = Job(
        id=uuid.uuid4(),
        identity_key="unit-failure-lease",
        kind="process_event",
        status="running",
        payload={"event_id": "event-1"},
        attempts=0,
        claimed_at=original_lease,
    )
    reloaded_job = Job(
        id=claimed_job.id,
        identity_key=claimed_job.identity_key,
        kind=claimed_job.kind,
        status="running",
        payload=claimed_job.payload,
        attempts=0,
        claimed_at=replacement_lease,
    )
    failed_leases: list[datetime] = []

    class Session:
        async def rollback(self) -> None:
            pass

        async def get(self, model: object, key: object) -> Job:
            assert model is Job
            assert key == claimed_job.id
            return reloaded_job

    async def claim(_session: object) -> Job:
        return claimed_job

    async def reject(*_args: object) -> None:
        raise ValueError("bounded-handler-failure")

    async def fail(
        _session: object, _job: Job, lease: datetime, _code: str
    ) -> bool:
        failed_leases.append(lease)
        return False

    monkeypatch.setattr(worker, "claim_next_job", claim)
    monkeypatch.setattr(worker, "handle", reject)
    monkeypatch.setattr(worker, "fail_job", fail)

    processed = anyio.run(worker.process_one, Session())  # type: ignore[arg-type]

    assert processed is True
    assert failed_leases == [original_lease]


@pytest.mark.parametrize(
    ("attempts", "expected_attempts", "expected_status"),
    [(0, 1, "queued"), (7, 8, "dead")],
)
def test_dml_failure_rolls_back_reloads_and_commits_safe_failure_state(
    monkeypatch: pytest.MonkeyPatch,
    attempts: int,
    expected_attempts: int,
    expected_status: str,
) -> None:
    """A poisoned materialization transaction cannot strand a running job."""
    from assistant_core.jobs import worker

    content_marker = "bounded-private-content-marker"
    event = completed_inbox_event(content_marker)
    job = Job(
        id=uuid.uuid4(),
        identity_key="unit-poisoned-dml",
        kind="process_event",
        status="running",
        payload={"event_id": event.event_id},
        attempts=attempts,
        claimed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )

    class PoisonedSession:
        poisoned = False
        rollback_count = 0
        successful_commit_count = 0
        reloaded_job = False
        execute_count = 0

        async def get(self, model: object, key: object) -> object:
            if model is EventInbox:
                assert key == event.event_id
                return event
            if model is Job:
                assert key == job.id
                self.reloaded_job = True
                return job
            pytest.fail("worker loaded an unexpected model")

        async def execute(self, statement: object) -> object:
            self.execute_count += 1
            if self.execute_count == 1:
                self.poisoned = True
                raise DBAPIError.instance(
                    "INSERT INTO assistant_core.completed_turn (...) VALUES (...) ",
                    {"assistant_content": content_marker},
                    Exception("database rejected completed turn"),
                    Exception,
                    hide_parameters=False,
                )
            assert self.poisoned is False
            params = statement.compile().params  # type: ignore[attr-defined]
            job.attempts = params["attempts"]
            job.status = params["status"]
            job.last_error_code = params["last_error_code"]

            class Result:
                rowcount = 1

            return Result()

        async def rollback(self) -> None:
            self.poisoned = False
            self.rollback_count += 1

        async def commit(self) -> None:
            if self.poisoned:
                raise PendingRollbackError("transaction is aborted")
            self.successful_commit_count += 1

    session = PoisonedSession()

    async def claim(_session: object) -> Job:
        return job

    monkeypatch.setattr(worker, "claim_next_job", claim)

    async def exercise() -> bool:
        try:
            return await worker.process_one(session)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - RED captures whether failure escaped
            return False

    processed = anyio.run(exercise)

    assert processed is True
    assert session.rollback_count == 1
    assert session.reloaded_job is True
    assert session.successful_commit_count == 1
    assert job.attempts == expected_attempts
    assert job.status == expected_status
    assert job.last_error_code == "handler_failed"


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

    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: Settings(
            database_url=database_url,
            task_model_base_url="https://task-model.example/v1",
            task_model_model="cheap-extractor",
        ),
    )
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
