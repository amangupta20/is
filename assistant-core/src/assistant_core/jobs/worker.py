"""Foundation worker for durable assistant jobs."""

import asyncio
import signal
import uuid
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import urlparse

import structlog
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.config import Settings, get_settings
from assistant_core.conversation.chunking import CHUNKING_VERSION
from assistant_core.conversation.embedder import (
    CONVERSATION_EMBEDDING_FAILED_ERROR,
    EMBEDDING_VERSION,
    ConversationEmbeddingError,
    EmbeddingConfigurationError,
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
    tombstone_chat,
)
from assistant_core.db.session import create_database
from assistant_core.events.models import EventInbox
from assistant_core.files.client import OpenWebUIFileFetchError, fetch_openwebui_file
from assistant_core.files.repository import (
    get_file_segment_content,
    materialize_file_passages,
    store_file_segment_embedding,
    tombstone_file_references,
)
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.jobs.repository import claim_next_job, complete_job, fail_job
from assistant_core.memory.consolidator import (
    MemoryConsolidationError,
    TaskModelMemoryConsolidator,
)
from assistant_core.memory.extractor import (
    MEMORY_EXTRACTION_FAILED_ERROR,
    CompletedTurnData,
    MemoryExtractionError,
    TaskModelMemoryExtractor,
)
from assistant_core.memory.repository import (
    apply_explicit_candidates,
    consolidate_user_memories,
)
from assistant_core.turns.models import CompletedTurn
from assistant_core.turns.repository import (
    INVALID_TURN_PAYLOAD_ERROR,
    InvalidTurnPayloadError,
    get_completed_turn_for_event,
    materialize_completed_turn,
)

UNSUPPORTED_KIND_ERROR = "unsupported_job_kind"
MEMORY_CONSOLIDATION_FAILED_ERROR = "memory_consolidation_failed"
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


def get_memory_consolidator(settings: Settings | None = None) -> TaskModelMemoryConsolidator:
    """Build the task-model memory consolidator."""
    resolved_settings = settings or get_settings()
    base_url, model = _task_model_configuration(resolved_settings)
    api_key = resolved_settings.task_model_api_key
    return TaskModelMemoryConsolidator(
        base_url=base_url,
        api_key=api_key.get_secret_value() if api_key is not None else None,
        model=model,
        timeout_seconds=resolved_settings.task_model_timeout_seconds,
    )


def get_conversation_embedder(
    settings: Settings | None = None,
) -> OpenAICompatibleEmbedder:
    """Return the configured OpenAI-compatible embedder for conversation recall."""
    resolved_settings = settings or get_settings()
    return build_conversation_embedder(resolved_settings)


async def _handle_process_event(session: AsyncSession, payload: dict[str, JsonValue]) -> None:
    """Route one received event to its durable materialization."""
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

    if event.event_type in {"file.deleted.v1", "file.deleted"}:
        file_id = event.payload.get("file_id") if isinstance(event.payload, dict) else None
        if file_id and isinstance(file_id, str):
            count = await tombstone_file_references(
                session, user_id=event.user_id, native_file_id=file_id
            )
            LOGGER.info(
                "file_tombstoned",
                file_id=file_id,
                user_id=str(event.user_id),
                tombstoned_count=count,
            )
        return

    if event.event_type in {"file.created.v1", "file.created", "file.attached.v1", "file.uploaded"}:
        file_id = event.payload.get("file_id") if isinstance(event.payload, dict) else None
        if file_id and isinstance(file_id, str):
            await session.execute(
                insert(Job)
                .values(
                    id=uuid.uuid4(),
                    identity_key=f"file:{file_id}:{CHUNKING_VERSION}",
                    kind="index_file",
                    status="queued",
                    payload={"file_id": file_id, "user_id": str(event.user_id)},
                    attempts=0,
                )
                .on_conflict_do_nothing(index_elements=[Job.identity_key])
            )
        return

    if event.event_type == "chat.deleted":
        native_chat_id = event.native_chat_id
        if not native_chat_id or not native_chat_id.strip():
            raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
        result = await tombstone_chat(
            session,
            user_id=event.user_id,
            native_chat_id=native_chat_id,
            occurred_at=event.occurred_at,
        )
        LOGGER.info(
            "conversation_chat_tombstoned",
            event_id=event.event_id,
            user_id=str(event.user_id),
            chat_id=native_chat_id,
            reference_count=result.reference_count,
            turn_count=result.turn_count,
            orphan_segment_count=result.orphan_segment_count,
        )
        return

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

        attached_files = (
            event.payload.get("attached_file_ids") if isinstance(event.payload, dict) else None
        )
        if isinstance(attached_files, list):
            for fid in attached_files:
                if isinstance(fid, str) and fid.strip():
                    await session.execute(
                        insert(Job)
                        .values(
                            id=uuid.uuid4(),
                            identity_key=f"file:{fid}:{CHUNKING_VERSION}",
                            kind="index_file",
                            status="queued",
                            payload={"file_id": fid, "user_id": str(event.user_id)},
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


async def _handle_extract_memory(session: AsyncSession, payload: dict[str, JsonValue]) -> None:
    """Extract after ending the DB read, then apply candidates in a fresh transaction."""
    turn_id = _turn_id_from_payload(payload)
    turn = await session.get(CompletedTurn, turn_id)
    if turn is None or turn.tombstoned_at is not None:
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    turn_data = CompletedTurnData(id=turn.id, user_content=turn.user_content)
    await session.rollback()
    candidates = await asyncio.to_thread(get_memory_extractor().extract, turn_data)
    fresh_turn = await session.get(CompletedTurn, turn_id)
    if fresh_turn is None or fresh_turn.tombstoned_at is not None:
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    await apply_explicit_candidates(session, fresh_turn, candidates)


async def enqueue_daily_consolidation_jobs(session: AsyncSession) -> int:
    """Enqueue at most one consolidation job per user per day."""
    user_stmt = select(UserIdentity.native_user_id).distinct()
    users = list((await session.execute(user_stmt)).scalars().all())
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    enqueued = 0
    for u in users:
        result = await session.execute(
            insert(Job)
            .values(
                id=uuid.uuid4(),
                identity_key=f"consolidate:{u}:{today_str}",
                kind="consolidate_memories",
                status="queued",
                payload={"native_user_id": u},
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=[Job.identity_key])
            .returning(Job.id)
        )
        if result.scalar_one_or_none() is not None:
            enqueued += 1
    return enqueued


async def _handle_consolidate_memories(
    session: AsyncSession, payload: dict[str, JsonValue]
) -> None:
    """Consolidate active memories for a user and record supersessions."""
    native_user_id = payload.get("native_user_id")
    if not isinstance(native_user_id, str) or not native_user_id.strip():
        return
    consolidator = get_memory_consolidator()
    applied = await consolidate_user_memories(
        session, native_user_id=native_user_id, consolidator=consolidator
    )
    LOGGER.info(
        "memories_consolidated",
        native_user_id=native_user_id,
        superseded_count=len(applied),
    )


async def _handle_index_conversation(session: AsyncSession, payload: dict[str, JsonValue]) -> None:
    """Commit lexical passages first, then enrich only missing vectors."""
    turn_id = _turn_id_from_payload(payload)
    turn = await session.get(CompletedTurn, turn_id)
    if turn is None or turn.tombstoned_at is not None:
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    started_at = perf_counter()
    turn_id_text = str(turn.id)
    user_id_text = str(turn.user_id)
    chat_id = turn.native_chat_id
    user_chars = len(turn.user_content)
    assistant_chars = len(turn.assistant_content)
    LOGGER.info(
        "conversation_index_started",
        turn_id=turn_id_text,
        user_id=user_id_text,
        chat_id=chat_id,
        user_chars=user_chars,
        assistant_chars=assistant_chars,
    )
    materialization = await materialize_turn_passages(session, turn)
    await session.commit()
    embedder: OpenAICompatibleEmbedder | None = None
    embedded_count = 0
    try:
        embedder = get_conversation_embedder()
        for segment_id in materialization.missing_embedding_ids:
            content = await get_segment_content(session, segment_id)
            if content is None:
                await session.rollback()
                continue
            await session.rollback()
            vector = await asyncio.to_thread(embedder.embed_one, content)
            await store_segment_embedding(
                session,
                segment_id,
                vector,
                model=embedder.model,
                dimension=embedder.dimension,
                version=EMBEDDING_VERSION,
                embedded_at=datetime.now(UTC),
            )
            await session.commit()
            embedded_count += 1
    except (EmbeddingConfigurationError, ConversationEmbeddingError):
        LOGGER.warning(
            "conversation_index_failed",
            turn_id=turn_id_text,
            user_id=user_id_text,
            chat_id=chat_id,
            missing_embedding_count=len(materialization.missing_embedding_ids),
            embedded_count=embedded_count,
            model=embedder.model if embedder is not None else None,
            dimension=embedder.dimension if embedder is not None else None,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            error_code=CONVERSATION_EMBEDDING_FAILED_ERROR,
        )
        raise

    LOGGER.info(
        "conversation_index_completed",
        turn_id=turn_id_text,
        user_id=user_id_text,
        chat_id=chat_id,
        new_segment_count=materialization.new_segments,
        reused_segment_count=materialization.reused_segments,
        new_reference_count=materialization.new_references,
        embedded_count=embedded_count,
        model=embedder.model,
        dimension=embedder.dimension,
        duration_ms=round((perf_counter() - started_at) * 1000, 3),
    )


async def _handle_index_file(session: AsyncSession, payload: dict[str, JsonValue]) -> None:
    """Fetch file markdown from Open WebUI, chunk, persist passages, and calculate missing embeddings."""
    file_id = payload.get("file_id")
    user_id_raw = payload.get("user_id")
    if not isinstance(file_id, str) or not file_id.strip() or not isinstance(user_id_raw, str):
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    try:
        user_id = uuid.UUID(user_id_raw)
    except ValueError:
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR) from None

    settings = get_settings()
    started_at = perf_counter()

    LOGGER.info(
        "file_index_started",
        file_id=file_id,
        user_id=str(user_id),
    )

    # Fetch file metadata and extracted content from Open WebUI
    file_info = await asyncio.to_thread(
        fetch_openwebui_file,
        base_url=settings.open_webui_url,
        api_key=settings.open_webui_api_key.get_secret_value()
        if settings.open_webui_api_key
        else None,
        file_id=file_id,
        timeout_seconds=settings.file_indexing_timeout_seconds,
    )
    if file_info is None:
        LOGGER.info("file_index_skipped_kb_document", file_id=file_id, user_id=str(user_id))
        return

    filename, mime_type, content = file_info

    materialization = await materialize_file_passages(
        session,
        user_id=user_id,
        native_file_id=file_id,
        filename=filename,
        mime_type=mime_type,
        markdown_text=content,
    )
    await session.commit()

    # Compute missing embeddings
    embedder: OpenAICompatibleEmbedder | None = None
    embedded_count = 0
    try:
        embedder = get_conversation_embedder()
        for seg_id_str in materialization.missing_embedding_segment_ids:
            seg_id = uuid.UUID(seg_id_str)
            seg_content = await get_file_segment_content(session, seg_id)
            if seg_content is None:
                await session.rollback()
                continue
            await session.rollback()
            vector = await asyncio.to_thread(embedder.embed_one, seg_content)
            await store_file_segment_embedding(
                session,
                seg_id,
                vector,
                model=embedder.model,
                dimension=embedder.dimension,
                version=EMBEDDING_VERSION,
            )
            await session.commit()
            embedded_count += 1
    except (EmbeddingConfigurationError, ConversationEmbeddingError) as exc:
        LOGGER.warning(
            "file_embedding_unavailable",
            file_id=file_id,
            error=str(exc),
        )
        await session.rollback()

    duration_ms = (perf_counter() - started_at) * 1000
    LOGGER.info(
        "file_index_completed",
        file_id=file_id,
        user_id=str(user_id),
        filename=filename,
        total_chunks=materialization.total_chunks,
        inserted_segments=materialization.inserted_segments,
        reused_segments=materialization.reused_segments,
        embedded_count=embedded_count,
        duration_ms=round(duration_ms, 2),
    )


async def handle(
    session: AsyncSession,
    kind: str,
    payload: dict[str, JsonValue],
) -> None:
    """Route one claimed payload to its task handler."""
    if kind == "process_event":
        await _handle_process_event(session, payload)
    elif kind == "extract_memory":
        await _handle_extract_memory(session, payload)
    elif kind == "consolidate_memories":
        await _handle_consolidate_memories(session, payload)
    elif kind == "index_conversation":
        await _handle_index_conversation(session, payload)
    elif kind == "index_file":
        await _handle_index_file(session, payload)
    else:
        raise UnsupportedJobKindError(UNSUPPORTED_KIND_ERROR)


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
    except MemoryConsolidationError:
        error_code = MEMORY_CONSOLIDATION_FAILED_ERROR
    except (ConversationEmbeddingError, EmbeddingConfigurationError):
        error_code = CONVERSATION_EMBEDDING_FAILED_ERROR
    except OpenWebUIFileFetchError as exc:
        error_code = (
            f"file_fetch_failed_{exc.status_code}" if exc.status_code else "file_fetch_failed"
        )
    except Exception:  # noqa: BLE001 - all ordinary handler failures share one safe code
        error_code = "handler_failed"
    else:
        if expected_claimed_at is None:
            raise InvalidJobClaimError(INVALID_JOB_CLAIM_ERROR) from None
        await complete_job(session, job_id, expected_claimed_at)
        return True

    if expected_claimed_at is None:
        raise InvalidJobClaimError(INVALID_JOB_CLAIM_ERROR) from None
    await _record_handler_failure(session, job_id, expected_claimed_at, error_code)
    return True


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Run the single-job polling loop until a graceful stop is requested."""
    settings = get_settings()
    _task_model_configuration(settings)
    engine, session_factory = create_database(settings.database_url)
    resolved_stop_event = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    registered_signals: list[signal.Signals] = []

    try:
        async with session_factory() as session:
            backfill_count = await enqueue_missing_conversation_jobs(session)
            consolidation_count = await enqueue_daily_consolidation_jobs(session)
            await session.commit()
        LOGGER.info(
            "worker_startup_jobs_enqueued",
            conversation_backfill=backfill_count,
            daily_consolidation=consolidation_count,
        )

        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, resolved_stop_event.set)
            except (NotImplementedError, RuntimeError):
                continue
            registered_signals.append(signum)

        last_consolidation_check = perf_counter()
        consolidation_interval_seconds = 3600.0  # Check hourly for daily job enqueue

        while not resolved_stop_event.is_set():
            now = perf_counter()
            if (now - last_consolidation_check) >= consolidation_interval_seconds:
                last_consolidation_check = now
                try:
                    async with session_factory() as session:
                        await enqueue_daily_consolidation_jobs(session)
                        await session.commit()
                except Exception:  # noqa: BLE001
                    LOGGER.warning("periodic_consolidation_enqueue_failed")

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
