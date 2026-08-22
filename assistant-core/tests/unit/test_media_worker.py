"""Unit tests for multimodal media worker job handling."""

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from assistant_core.config import Settings
from assistant_core.events.models import EventInbox
from assistant_core.jobs.worker import (
    InvalidTurnPayloadError,
    get_media_analyzer,
    handle,
)
from assistant_core.media.analyzer import MediaAnalyzer
from assistant_core.media.models import MediaDocument
from assistant_core.media.schemas import MediaAnalysisResult, MediaSegmentAnalysis
from assistant_core.turns.models import CompletedTurn


class MockSession:
    def __init__(self, turns: list[CompletedTurn] | None = None, event: EventInbox | None = None) -> None:
        self.turns = turns or []
        self.event = event
        self.added: list[object] = []
        self.committed = False
        self.rolled_back = False
        self.statements: list[object] = []

    def add(self, item: object) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def flush(self) -> None:
        pass

    async def get(self, model: type, pk: object) -> object | None:
        if model is EventInbox and self.event is not None and self.event.event_id == pk:
            return self.event
        if model is CompletedTurn:
            for t in self.turns:
                if t.id == pk:
                    return t
        return None

    async def execute(self, stmt: object) -> object:
        self.statements.append(stmt)

        class ExecResult:
            def __init__(self, turns: list[CompletedTurn]) -> None:
                self._turns = turns

            def scalar_one_or_none(self) -> object | None:
                if self._turns:
                    return self._turns[0]
                return None

            def scalars(self) -> "ExecResult":
                return self

            def all(self) -> list[object]:
                return self._turns

        return ExecResult(self.turns)


def _make_completed_event(user_content: str, assistant_content: str = "Assistant reply") -> EventInbox:
    user_hash = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
    asst_hash = hashlib.sha256(assistant_content.encode("utf-8")).hexdigest()
    return EventInbox(
        event_id="test-event-123",
        event_type="turn.completed.v1",
        user_id=uuid.uuid4(),
        native_chat_id="chat-abc",
        native_message_id="msg-asst-1",
        occurred_at=datetime.now(UTC),
        payload={
            "source": "openwebui",
            "user_message": {
                "id": "msg-user-1",
                "role": "user",
                "content": user_content,
                "sha256": user_hash,
            },
            "assistant_message": {
                "id": "msg-asst-1",
                "role": "assistant",
                "content": assistant_content,
                "sha256": asst_hash,
            },
        },
    )


def test_get_media_analyzer_factory() -> None:
    """Verify get_media_analyzer constructs MediaAnalyzer with configured settings."""
    settings = Settings(
        task_model_base_url="https://test.litellm.local/v1",
        task_model_api_key=SecretStr("secret-key"),
        task_model_model="gemini-2.0-flash",
        task_model_timeout_seconds=90.0,
    )
    analyzer = get_media_analyzer(settings)
    assert isinstance(analyzer, MediaAnalyzer)
    assert analyzer._base_url == "https://test.litellm.local/v1"
    assert analyzer._api_key == "secret-key"
    assert analyzer._model == "gemini-2.0-flash"
    assert analyzer._timeout_seconds == 120.0


@pytest.mark.anyio
async def test_process_event_enqueues_index_media_on_youtube_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify turn.completed.v1 event with YouTube URL enqueues index_media job."""
    user_msg = "Check out this video: https://www.youtube.com/watch?v=dQw4w9WgXcQ and https://youtu.be/sample123"
    event = _make_completed_event(user_content=user_msg)
    turn = CompletedTurn(
        id=uuid.uuid4(),
        event_id=event.event_id,
        user_id=event.user_id,
        native_chat_id="chat-abc",
        native_user_message_id="msg-user-1",
        native_assistant_message_id="msg-asst-1",
        user_content=user_msg,
        assistant_content="Sure!",
        user_content_sha256="hash1",
        assistant_content_sha256="hash2",
    )

    session = MockSession(turns=[turn], event=event)

    async def mock_materialize(*args: object, **kwargs: object) -> bool:
        return True

    async def mock_get_turn(*args: object, **kwargs: object) -> CompletedTurn:
        return turn

    monkeypatch.setattr(
        "assistant_core.jobs.worker.materialize_completed_turn", mock_materialize
    )
    monkeypatch.setattr(
        "assistant_core.jobs.worker.get_completed_turn_for_event", mock_get_turn
    )

    await handle(session, "process_event", {"event_id": event.event_id})
    media_stmts = [
        s for s in session.statements
        if hasattr(s, "compile") and s.compile().params.get("kind") == "index_media"
    ]
    assert len(media_stmts) == 2
    keys = {s.compile().params.get("identity_key") for s in media_stmts}
    assert keys == {
        "media:https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "media:https://youtu.be/sample123",
    }


@pytest.mark.anyio
async def test_process_event_no_youtube_url_skips_index_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify turn.completed.v1 event without YouTube URL does not enqueue index_media job."""
    user_msg = "Hello, how are you today?"
    event = _make_completed_event(user_content=user_msg)
    turn = CompletedTurn(
        id=uuid.uuid4(),
        event_id=event.event_id,
        user_id=event.user_id,
        native_chat_id="chat-abc",
        native_user_message_id="msg-user-1",
        native_assistant_message_id="msg-asst-1",
        user_content=user_msg,
        assistant_content="Doing well!",
        user_content_sha256="hash1",
        assistant_content_sha256="hash2",
    )

    session = MockSession(turns=[turn], event=event)

    async def mock_materialize(*args: object, **kwargs: object) -> bool:
        return True

    async def mock_get_turn(*args: object, **kwargs: object) -> CompletedTurn:
        return turn

    monkeypatch.setattr(
        "assistant_core.jobs.worker.materialize_completed_turn", mock_materialize
    )
    monkeypatch.setattr(
        "assistant_core.jobs.worker.get_completed_turn_for_event", mock_get_turn
    )
    await handle(session, "process_event", {"event_id": event.event_id})

    media_stmts = [
        s for s in session.statements
        if hasattr(s, "compile") and s.compile().params.get("kind") == "index_media"
    ]
    assert len(media_stmts) == 0


@pytest.mark.anyio
async def test_handle_index_media_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify _handle_index_media runs analyzer, stores analysis, and commits."""
    user_id = uuid.uuid4()
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    session = MockSession()

    mock_analysis = MediaAnalysisResult(
        url=url,
        media_type="youtube",
        title="Rick Astley - Never Gonna Give You Up",
        description="Music Video",
        channel_or_author="Rick Astley",
        duration_seconds=213,
        summary="A legendary music video.",
        key_takeaways=["Classic track"],
        topics=["music"],
        segments=[
            MediaSegmentAnalysis(
                segment_index=0,
                start_time_seconds=0,
                end_time_seconds=60,
                label="Intro",
                content="Verse 1.",
            )
        ],
    )

    class FakeAnalyzer:
        async def analyze_media(self, url: str) -> MediaAnalysisResult:
            return mock_analysis

    monkeypatch.setattr(
        "assistant_core.jobs.worker.get_media_analyzer", lambda *args, **kwargs: FakeAnalyzer()
    )

    class FakeEmbedder:
        def embed_one(self, text: str) -> list[float]:
            return [0.1] * 1536

    monkeypatch.setattr(
        "assistant_core.jobs.worker.get_conversation_embedder",
        lambda *args, **kwargs: FakeEmbedder(),
    )

    doc = MediaDocument(
        id=uuid.uuid4(),
        user_id=user_id,
        url=url,
        title=mock_analysis.title,
        summary=mock_analysis.summary,
    )

    async def mock_store(*args: object, **kwargs: object) -> MediaDocument:
        return doc

    monkeypatch.setattr(
        "assistant_core.jobs.worker.store_media_analysis", mock_store
    )

    await handle(
        session,
        "index_media",
        {"url": url, "user_id": str(user_id)},
    )

    assert session.committed is True


@pytest.mark.anyio
async def test_handle_index_media_invalid_payload() -> None:
    """Verify _handle_index_media raises InvalidTurnPayloadError on missing or invalid fields."""
    session = MockSession()

    with pytest.raises(InvalidTurnPayloadError):
        await handle(session, "index_media", {"url": "https://youtu.be/abc"})

    with pytest.raises(InvalidTurnPayloadError):
        await handle(session, "index_media", {"url": "", "user_id": "not-a-uuid"})
