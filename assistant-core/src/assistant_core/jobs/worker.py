"""Foundation worker for durable assistant jobs."""

import asyncio
import signal
import uuid

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.config import get_settings
from assistant_core.db.session import create_database
from assistant_core.events.models import EventInbox
from assistant_core.jobs.models import Job
from assistant_core.jobs.repository import claim_next_job, complete_job, fail_job
from assistant_core.turns.repository import (
    INVALID_TURN_PAYLOAD_ERROR,
    InvalidTurnPayloadError,
    materialize_completed_turn,
)

UNSUPPORTED_KIND_ERROR = "unsupported_job_kind"
CLAIMED_JOB_MISSING_ERROR = "claimed_job_missing"
IDLE_POLL_SECONDS = 1.0


class UnsupportedJobKindError(ValueError):
    """Raised when a worker receives a job kind it cannot process."""


class ClaimedJobMissingError(RuntimeError):
    """Raised safely when a claimed job disappears before failure persistence."""


async def handle(
    session: AsyncSession, kind: str, payload: dict[str, JsonValue]
) -> None:
    """Route one job, materializing only completed-turn inbox events."""
    if kind != "process_event":
        raise UnsupportedJobKindError(UNSUPPORTED_KIND_ERROR)

    event_id = payload.get("event_id")
    if (
        set(payload) != {"event_id"}
        or not isinstance(event_id, str)
        or not event_id.strip()
        or len(event_id) > 200
    ):
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)

    event = await session.get(EventInbox, event_id)
    if event is None:
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    if event.event_type == "turn.completed.v1":
        await materialize_completed_turn(session, event)


async def _record_handler_failure(
    session: AsyncSession, job_id: uuid.UUID, error_code: str
) -> None:
    """Recover a failed transaction and persist safe lifecycle state."""
    await session.rollback()
    job = await session.get(Job, job_id)
    if job is None:
        raise ClaimedJobMissingError(CLAIMED_JOB_MISSING_ERROR) from None
    await fail_job(session, job, error_code)


async def process_one(session: AsyncSession) -> bool:
    """Claim and process at most one job from an open worker session."""
    job = await claim_next_job(session)
    if job is None:
        return False

    job_id = job.id
    error_code: str
    try:
        await handle(session, job.kind, job.payload)
    except InvalidTurnPayloadError:
        error_code = INVALID_TURN_PAYLOAD_ERROR
    except Exception:  # noqa: BLE001 - all ordinary handler failures share one safe code
        error_code = "handler_failed"
    else:
        await complete_job(session, job)
        return True

    await _record_handler_failure(session, job_id, error_code)
    return True


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Run the single-job polling loop until a graceful stop is requested."""
    settings = get_settings()
    engine, session_factory = create_database(settings.database_url)
    resolved_stop_event = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    registered_signals: list[signal.Signals] = []

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, resolved_stop_event.set)
            except (NotImplementedError, RuntimeError):
                continue
            registered_signals.append(signum)

        while not resolved_stop_event.is_set():
            async with session_factory() as session:
                processed = await process_one(session)

            if not processed:
                try:
                    await asyncio.wait_for(
                        resolved_stop_event.wait(),
                        timeout=IDLE_POLL_SECONDS,
                    )
                except TimeoutError:
                    pass
    finally:
        for signum in registered_signals:
            loop.remove_signal_handler(signum)
        await engine.dispose()


def main() -> None:
    """Run the worker module as a standalone process."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
