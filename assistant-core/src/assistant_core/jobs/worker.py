"""Foundation worker for durable assistant jobs."""

import asyncio
import signal
import uuid
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import urlparse

import structlog
from pydantic import JsonValue
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.config import Settings, get_settings
from assistant_core.conversation.chunking import CHUNKING_VERSION
from assistant_core.conversation.embedder import (
    CONVERSATION_EMBEDDING_FAILED_ERROR,
    EMBEDDING_VERSION,
    ConversationEmbeddingError,
    OpenAICompatibleEmbedder,
)
from assistant_core.conversation.embedder import (
    get_conversation_embedder as build_conversation_embedder,
)
from assistant_core.conversation.repository import (
    enqueue_missing_conversation_jobs,
    get_segment_content,
    materialize_turn_passages,
    store_segment_embedding,
)
from assistant_core.db.session import create_database
from assistant_core.events.models import EventInbox
from assistant_core.jobs.models import Job
from assistant_core.jobs.repository import claim_next_job, complete_job, fail_job
from assistant_core.memory.extractor import (
    MEMORY_EXTRACTION_FAILED_ERROR,
    CompletedTurnData,
    MemoryExtractionError,
    TaskModelMemoryExtractor,
)
from assistant_core.memory.repository import apply_explicit_candidates
from assistant_core.turns.models import CompletedTurn
from assistant_core.turns.repository import (
    INVALID_TURN_PAYLOAD_ERROR,
    InvalidTurnPayloadError,
    get_completed_turn_for_event,
    materialize_completed_turn,
)

UNSUPPORTED_KIND_ERROR = "unsupported_job_kind"
CLAIMED_JOB_MISSING_ERROR = "claimed_job_missing"
INVALID_JOB_CLAIM_ERROR = "invalid_job_claim"
TASK_MODEL_CONFIGURATION_ERROR = "task_model_configuration_error"
IDLE_POLL_SECONDS = 1.0
LOGGER = structlog.get_logger("assistant_core.worker")


class UnsupportedJobKindError(ValueError):
    """Raised when a worker receives a job kind it cannot process."""


class ClaimedJobMissingError(RuntimeError):
    """Raised safely when a claimed job disappears before failure persistence."""


class InvalidJobClaimError(RuntimeError):
    """Raised safely when a claimed job has no usable lease value."""


class TaskModelConfigurationError(ValueError):
    """Raised with one content-free code for missing worker task-model config."""


def _task_model_configuration(settings: Settings) -> tuple[str, str]:
    """Return only a usable endpoint and model name for the extraction worker."""
    base_url = settings.task_model_base_url
    model = settings.task_model_model
    parsed = urlparse(base_url) if base_url else None
    if (
        not base_url
        or not model
        or not base_url.strip()
        or not model.strip()
        or parsed is None
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
    ):
        raise TaskModelConfigurationError(TASK_MODEL_CONFIGURATION_ERROR)
    return base_url, model


def get_memory_extractor(settings: Settings | None = None) -> TaskModelMemoryExtractor:
    """Build the single cheap-model client after its worker settings are usable."""
    resolved_settings = settings or get_settings()
    base_url, model = _task_model_configuration(resolved_settings)
    api_key = resolved_settings.task_model_api_key
    return TaskModelMemoryExtractor(
        base_url=base_url,
        api_key=api_key.get_secret_value() if api_key is not None else None,
        model=model,
        timeout_seconds=resolved_settings.task_model_timeout_seconds,
    )


def get_conversation_embedder(
    settings: Settings | None = None,
) -> OpenAICompatibleEmbedder:
    """Build the configured one-input embedding client."""
    return build_conversation_embedder(settings or get_settings())


async def handle(
    session: AsyncSession, kind: str, payload: dict[str, JsonValue]
) -> None:
    """Route one job without calling the provider for event materialization."""
    if kind == "extract_memory":
        await _handle_extract_memory(session, payload)
        return
    if kind == "index_conversation":
        await _handle_index_conversation(session, payload)
        return
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
        turn = await get_completed_turn_for_event(session, event.event_id)
        await session.execute(
            insert(Job)
            .values(
                id=uuid.uuid4(),
                identity_key=f"memory:{turn.id}",
                kind="extract_memory",
                status="queued",
                payload={"turn_id": str(turn.id)},
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=[Job.identity_key])
        )
        await session.execute(
            insert(Job)
            .values(
                id=uuid.uuid4(),
                identity_key=f"conversation:{turn.id}:{CHUNKING_VERSION}",
                kind="index_conversation",
                status="queued",
                payload={"turn_id": str(turn.id)},
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=[Job.identity_key])
        )


def _turn_id_from_payload(payload: dict[str, JsonValue]) -> uuid.UUID:
    """Validate the one identifier allowed in an extraction job payload."""
    turn_id = payload.get("turn_id")
    if set(payload) != {"turn_id"} or not isinstance(turn_id, str):
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    try:
        return uuid.UUID(turn_id)
    except ValueError:
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR) from None


async def _handle_extract_memory(
    session: AsyncSession, payload: dict[str, JsonValue]
) -> None:
    """Extract after ending the DB read, then apply candidates in a fresh transaction."""
    turn_id = _turn_id_from_payload(payload)
    turn = await session.get(CompletedTurn, turn_id)
    if turn is None:
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    turn_data = CompletedTurnData(id=turn.id, user_content=turn.user_content)
    await session.rollback()
    candidates = await asyncio.to_thread(get_memory_extractor().extract, turn_data)
    fresh_turn = await session.get(CompletedTurn, turn_id)
    if fresh_turn is None:
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    await apply_explicit_candidates(session, fresh_turn, candidates)


async def _handle_index_conversation(
    session: AsyncSession, payload: dict[str, JsonValue]
) -> None:
    """Commit lexical passages first, then enrich only missing vectors."""
    turn_id = _turn_id_from_payload(payload)
    turn = await session.get(CompletedTurn, turn_id)
    if turn is None or turn.tombstoned_at is not None:
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    started_at = perf_counter()
    LOGGER.info(
        "conversation_index_started",
        turn_id=str(turn.id),
        user_id=str(turn.user_id),
        chat_id=turn.native_chat_id,
        user_chars=len(turn.user_content),
        assistant_chars=len(turn.assistant_content),
    )
    materialization = await materialize_turn_passages(session, turn)
    await session.commit()
    embedder = get_conversation_embedder()
    embedded_count = 0
    try:
        for segment_id in materialization.missing_embedding_ids:
            content = await get_segment_content(session, segment_id)
            if content is None:
                await session.rollback()
                continue
            await session.rollback()
            embedding = await asyncio.to_thread(embedder.embed_one, content)
            stored = await store_segment_embedding(
                session,
                segment_id,
                embedding,
                model=embedder.model,
                dimension=embedder.dimension,
                version=EMBEDDING_VERSION,
                embedded_at=datetime.now(UTC),
            )
            await session.commit()
            embedded_count += int(stored)
    except ConversationEmbeddingError:
        LOGGER.warning(
            "conversation_index_failed",
            turn_id=str(turn.id),
            user_id=str(turn.user_id),
            chat_id=turn.native_chat_id,
            missing_embedding_count=len(materialization.missing_embedding_ids),
            embedded_count=embedded_count,
            model=embedder.model,
            dimension=embedder.dimension,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            error_code=CONVERSATION_EMBEDDING_FAILED_ERROR,
        )
        raise
    LOGGER.info(
        "conversation_index_completed",
        turn_id=str(turn.id),
        user_id=str(turn.user_id),
        chat_id=turn.native_chat_id,
        new_segment_count=materialization.new_segments,
        reused_segment_count=materialization.reused_segments,
        new_reference_count=materialization.new_references,
        embedded_count=embedded_count,
        model=embedder.model,
        dimension=embedder.dimension,
        duration_ms=round((perf_counter() - started_at) * 1000, 3),
    )


async def _record_handler_failure(
    session: AsyncSession,
    job_id: uuid.UUID,
    expected_claimed_at: datetime,
    error_code: str,
) -> None:
    """Recover a failed transaction and persist safe lifecycle state."""
    await session.rollback()
    job = await session.get(Job, job_id)
    if job is None:
        raise ClaimedJobMissingError(CLAIMED_JOB_MISSING_ERROR) from None
    await fail_job(session, job, expected_claimed_at, error_code)


async def process_one(session: AsyncSession) -> bool:
    """Claim and process at most one job from an open worker session."""
    job = await claim_next_job(session)
    if job is None:
        return False

    job_id = job.id
    expected_claimed_at = job.claimed_at
    error_code: str
    try:
        await handle(session, job.kind, job.payload)
    except InvalidTurnPayloadError:
        error_code = INVALID_TURN_PAYLOAD_ERROR
    except MemoryExtractionError:
        error_code = MEMORY_EXTRACTION_FAILED_ERROR
    except ConversationEmbeddingError:
        error_code = CONVERSATION_EMBEDDING_FAILED_ERROR
    except Exception:  # noqa: BLE001 - all ordinary handler failures share one safe code
        error_code = "handler_failed"
    else:
        if expected_claimed_at is None:
            raise InvalidJobClaimError(INVALID_JOB_CLAIM_ERROR) from None
        await complete_job(session, job_id, expected_claimed_at)
        return True

    if expected_claimed_at is None:
        raise InvalidJobClaimError(INVALID_JOB_CLAIM_ERROR) from None
    await _record_handler_failure(
        session, job_id, expected_claimed_at, error_code
    )
    return True


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Run the single-job polling loop until a graceful stop is requested."""
    settings = get_settings()
    _task_model_configuration(settings)
    build_conversation_embedder(settings)
    engine, session_factory = create_database(settings.database_url)
    resolved_stop_event = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    registered_signals: list[signal.Signals] = []

    try:
        async with session_factory() as session:
            backfill_count = await enqueue_missing_conversation_jobs(session)
            await session.commit()
        LOGGER.info("conversation_backfill_enqueued", job_count=backfill_count)

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
