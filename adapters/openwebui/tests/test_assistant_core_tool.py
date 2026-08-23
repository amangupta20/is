"""Contract tests for the self-contained Assistant Core status Tool."""

import asyncio
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from typing import Any, Self

import pytest
from pydantic import ValidationError

UNAVAILABLE = "Assistant Core is unavailable. Ordinary chat can continue without custom context."


def _module():
    path = Path(__file__).parents[1] / "assistant_core_tool.py"
    if not path.exists():
        pytest.fail("Assistant Core Tool module is missing")
    spec = importlib.util.spec_from_file_location("assistant_core_tool", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, response_json: object) -> None:
        self.response_json = response_json

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.response_json


class _RecordingClient:
    def __init__(self, capture: dict[str, Any], timeout: float, response_json: object) -> None:
        capture["timeout"] = timeout
        self.capture = capture
        self.response_json = response_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self.capture["url"] = url
        self.capture.update(kwargs)
        return _Response(self.response_json)


def test_assistant_status_signs_exact_identity_body_and_formats_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient(
            capture,
            timeout,
            {"status": "ok", "queued_jobs": 12, "dead_jobs": 3},
        ),
    )
    monkeypatch.setattr(module.time, "time", lambda: 1_800_000_100)
    tool = module.Tools()
    tool.valves.hmac_secret = "tool-test-secret"

    result = asyncio.run(
        tool.assistant_status(
            __user__={"id": 42, "name": "not-forwarded"},
            __metadata__={
                "chat_id": 123,
                "message_id": 456,
                "prompt": "not-forwarded",
            },
        )
    )

    assert result == "Assistant Core: ok; queued jobs: 12; dead jobs: 3."
    assert capture["timeout"] == 3.0
    assert capture["url"] == "http://assistant-core:8080/v1/status"
    body = capture["content"]
    assert json.loads(body) == {
        "native_user_id": "42",
        "native_chat_id": "123",
        "native_message_id": "456",
    }
    assert body == json.dumps(json.loads(body), separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"POST\n/v1/status\n1800000100\n{digest}".encode()
    expected = hmac.new(b"tool-test-secret", canonical, hashlib.sha256).hexdigest()
    assert capture["headers"] == {
        "content-type": "application/json",
        "x-assistant-timestamp": "1800000100",
        "x-assistant-signature": expected,
    }


def test_missing_openwebui_identity_uses_unknown_and_null_optional_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient(
            capture,
            timeout,
            {"status": "ok", "queued_jobs": 0, "dead_jobs": 0},
        ),
    )

    result = asyncio.run(module.Tools().assistant_status())

    assert result == "Assistant Core: ok; queued jobs: 0; dead jobs: 0."
    assert json.loads(capture["content"]) == {
        "native_user_id": "unknown",
        "native_chat_id": None,
        "native_message_id": None,
    }


@pytest.mark.parametrize(
    "response_json",
    [
        None,
        [],
        {},
        {"status": "not-ok", "queued_jobs": 0, "dead_jobs": 0},
        {"status": "ok", "queued_jobs": -1, "dead_jobs": 0},
        {"status": "ok", "queued_jobs": 0, "dead_jobs": -1},
        {"status": "ok", "queued_jobs": True, "dead_jobs": 0},
        {"status": "ok", "queued_jobs": 0, "dead_jobs": False},
        {"status": "ok", "queued_jobs": 0.0, "dead_jobs": 0},
        {"status": "ok", "queued_jobs": 0, "dead_jobs": "0"},
        {"status": "ok", "queued_jobs": 0, "dead_jobs": 0, "details": "private"},
    ],
)
def test_malformed_status_response_returns_exact_unavailable_message(
    response_json: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient({}, timeout, response_json),
    )

    assert asyncio.run(module.Tools().assistant_status()) == UNAVAILABLE


def test_network_failure_returns_exact_unavailable_without_leaking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()

    class _FailingClient(_RecordingClient):
        async def post(self, url: str, **kwargs: Any) -> _Response:
            raise RuntimeError("SECRET response body and URL must never appear")

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _FailingClient({}, timeout, {}),
    )

    assert asyncio.run(module.Tools().assistant_status()) == UNAVAILABLE


def test_personal_context_tools_show_profile_search_read_and_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    source_id = "00000000-0000-0000-0000-000000000011"
    responses = {
        "/v1/context": {
            "context_text": "<user_profile>\n- Use direct answers.\n</user_profile>",
            "token_estimate": 13,
            "sources": [{"source_type": "memory", "source_id": source_id, "label": "profile"}],
            "degraded": False,
        },
        "/v1/personal-context/search": {
            "mode": "hybrid",
            "results": [
                {
                    "source_id": source_id,
                    "source_type": "memory",
                    "category": "preference",
                    "role": None,
                    "preview": "Use direct answers.",
                    "source_native_chat_id": None,
                    "source_native_message_id": None,
                },
                {
                    "source_id": "00000000-0000-0000-0000-000000000012",
                    "source_type": "conversation",
                    "category": "conversation_evidence",
                    "role": "assistant",
                    "preview": "We used bounded hybrid recall.",
                    "source_native_chat_id": "chat-2",
                    "source_native_message_id": "message-2",
                },
            ],
        },
        "/v1/personal-context/read": {
            "source_id": source_id,
            "source_type": "memory",
            "content": "Use direct answers.",
            "category": "preference",
            "role": None,
            "evidence_quote": "I prefer direct answers.",
            "source_native_chat_id": "chat-1",
            "source_native_message_id": "message-1",
            "neighbors": [],
            "full_source_available": False,
        },
    }

    class _RoutingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: Any) -> _Response:
            return _Response(responses["/" + url.split("/", 3)[3]])

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *, timeout: _RoutingClient())
    tool = module.Tools()
    tool.valves.hmac_secret = "tool-test-secret"
    user = {"id": "user-1"}
    metadata = {"chat_id": "chat-1", "message_id": "message-1"}

    assert asyncio.run(tool.show_loaded_profile(__user__=user, __metadata__=metadata)) == (
        f"<user_profile>\n- Use direct answers.\n</user_profile>\nProfile source IDs: {source_id}."
    )
    assert asyncio.run(
        tool.search_personal_context("direct", __user__=user, __metadata__=metadata)
    ) == (
        "Personal context search (hybrid):\n"
        f"- [memory/preference] {source_id}: Use direct answers.\n"
        "- [conversation/assistant evidence] 00000000-0000-0000-0000-000000000012: "
        "We used bounded hybrid recall. (chat chat-2, message message-2)"
    )
    assert asyncio.run(tool.read_personal_context(source_id, __user__=user)) == (
        f"Personal memory source {source_id}:\n"
        "Content: Use direct answers.\n"
        "Category: preference\n"
        'Evidence: "I prefer direct answers."\n'
        "Source chat: chat-1\n"
        "Source message: message-1\n"
        "Neighboring context: none\n"
        "Full-source expansion available: no"
    )

    class _FailingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, **_kwargs: Any) -> _Response:
            raise TimeoutError("private response must not escape")

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *, timeout: _FailingClient())
    assert asyncio.run(tool.read_personal_context(source_id, __user__=user)) == UNAVAILABLE


def test_tool_valves_are_json_persistable_and_password_marked() -> None:
    module = _module()
    valves = module.Tools.Valves()

    assert json.loads(valves.model_dump_json()) == {
        "assistant_core_url": "http://assistant-core:8080",
        "hmac_secret": "development-hmac-secret-change-me",
        "timeout_seconds": 3.0,
    }
    schema = module.Tools.Valves.model_json_schema()
    assert schema["properties"]["hmac_secret"]["input"] == {"type": "password"}
    with pytest.raises(ValidationError):
        module.Tools.Valves(timeout_seconds=0.09)
    with pytest.raises(ValidationError):
        module.Tools.Valves(timeout_seconds=10.01)


def test_show_file_index_status_and_failed_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    responses = {
        "/v1/inspection/files/stats": {
            "total_files": 3,
            "active_files": 2,
            "tombstoned_files": 1,
            "total_segments": 15,
            "embedded_segments": 15,
            "lexical_segments": 0,
            "reused_segments": 2,
            "queued_jobs": 0,
            "dead_jobs": 0,
            "last_indexed_at": "2026-08-16T12:00:00Z",
        },
        "/v1/inspection/files/recent": {
            "files": [
                {
                    "native_file_id": "file-1",
                    "filename": "specs.pdf",
                    "mime_type": "application/pdf",
                    "chunk_count": 8,
                    "created_at": "2026-08-16T12:00:00Z",
                    "tombstoned_at": None,
                    "status": "indexed",
                }
            ]
        },
        "/v1/inspection/jobs/dead": {
            "dead_jobs": [
                {
                    "job_id": "00000000-0000-0000-0000-000000000099",
                    "kind": "index_file",
                    "identity_key": "file:test-doc:markdown-v1",
                    "attempts": 8,
                    "last_error": "file_fetch_failed_404",
                    "available_at": "2026-08-16T12:00:00Z",
                    "claimed_at": "2026-08-16T12:05:00Z",
                }
            ]
        },
    }

    class _RoutingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: Any) -> _Response:
            path = "/" + url.split("/", 3)[3]
            return _Response(responses[path])

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *, timeout: _RoutingClient())
    tool = module.Tools()
    tool.valves.hmac_secret = "tool-test-secret"
    user = {"id": "user-1"}

    status_out = asyncio.run(tool.show_file_index_status(__user__=user))
    assert "File Index Stats: 3 files (2 active, 1 deleted)" in status_out
    assert "specs.pdf (8 chunks, status: indexed)" in status_out

    dead_out = asyncio.run(tool.show_failed_indexing_jobs(__user__=user))
    assert "Found 1 failed background jobs:" in dead_out
    assert "file_fetch_failed_404" in dead_out


def test_read_full_document(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    responses = {
        "/v1/personal-context/file-content": {
            "native_file_id": "file-123",
            "filename": "specs.pdf",
            "mime_type": "application/pdf",
            "total_chunks": 5,
            "total_characters": 1200,
            "content": "# System Architecture\nComplete text here.",
        }
    }

    class _RoutingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: Any) -> _Response:
            path = "/" + url.split("/", 3)[3]
            return _Response(responses[path])

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *, timeout: _RoutingClient())
    tool = module.Tools()
    tool.valves.hmac_secret = "tool-test-secret"
    user = {"id": "user-1"}

    out = asyncio.run(tool.read_full_document("specs.pdf", __user__=user))
    assert "Full Document: specs.pdf" in out
    assert "Chunks: 5" in out
    assert "# System Architecture\nComplete text here." in out


def test_media_context_search_read_and_process_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    source_id = "00000000-0000-0000-0000-000000000099"
    responses = {
        "/v1/personal-context/search": {
            "mode": "hybrid",
            "results": [
                {
                    "source_id": source_id,
                    "source_type": "media",
                    "category": "media_segment",
                    "role": None,
                    "preview": "Neural networks learn representations through backpropagation.",
                    "title": "Deep Learning Fundamentals",
                    "start_time_seconds": 125,
                    "end_time_seconds": 240,
                    "label": "Backprop Chapter",
                }
            ],
        },
        "/v1/personal-context/read": {
            "source_id": source_id,
            "source_type": "media",
            "title": "Deep Learning Fundamentals",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "category": "media_segment",
            "start_time_seconds": 125,
            "end_time_seconds": 240,
            "content": "# Media: Deep Learning Fundamentals\nDetailed transcript of gradient descent.",
        },
        "/v1/personal-context/process-media": {
            "status": "queued",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "media_type": "youtube",
        },
    }

    class _RoutingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: Any) -> _Response:
            path = "/" + url.split("/", 3)[3]
            return _Response(responses[path])

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *, timeout: _RoutingClient())
    tool = module.Tools()
    tool.valves.hmac_secret = "tool-test-secret"
    user = {"id": "user-1"}

    search_out = asyncio.run(tool.search_personal_context("backprop", __user__=user))
    assert "media/Deep Learning Fundamentals @ 02:05-04:00" in search_out
    assert "(Backprop Chapter)" in search_out

    read_out = asyncio.run(tool.read_personal_context(source_id, __user__=user))
    assert f"Media source {source_id}:" in read_out
    assert "Media: media/Deep Learning Fundamentals @ 02:05-04:00" in read_out
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in read_out
    assert "Detailed transcript of gradient descent." in read_out

    process_out = asyncio.run(tool.process_media_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ", __user__=user))
    assert "Queued for deep media indexing" in process_out
    assert "search_personal_context" in process_out

    duplicate_payloads = {
        "/v1/personal-context/process-media": {
            "status": "duplicate",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "media_type": "youtube",
        }
    }

    class _DuplicateClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: Any) -> _Response:
            path = "/" + url.split("/", 3)[3]
            return _Response(duplicate_payloads[path])

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *, timeout: _DuplicateClient())
    dup_out = asyncio.run(
        tool.process_media_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ", __user__=user)
    )
    assert "already indexed or queued" in dup_out


def test_media_time_str_handles_fractional_zero_and_invalid_seconds() -> None:
    module = _module()
    fmt = module.Tools._media_time_str

    assert fmt(125, 240) == " @ 02:05-04:00"
    assert fmt(125.7, 240.2) == " @ 02:05-04:00"
    assert fmt(125, None) == " @ 02:05"
    assert fmt(0, 0) == " @ 00:00"
    assert fmt(0, None) == " @ 00:00"
    assert fmt(3600.0, 3661.5) == " @ 60:00-61:01"
    assert fmt(None, 10) == ""
    assert fmt("bad", 10) == ""
    assert fmt(True, 5) == ""


def test_media_search_read_survive_fractional_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    source_id = "00000000-0000-0000-0000-000000000099"
    fractional_payloads = {
        "/v1/personal-context/search": {
            "mode": "hybrid",
            "results": [
                {
                    "source_id": source_id,
                    "source_type": "media",
                    "category": "media_segment",
                    "role": None,
                    "preview": "Neural networks learn representations.",
                    "title": "Deep Learning Fundamentals",
                    "start_time_seconds": 125.42,
                    "end_time_seconds": 240.86,
                }
            ],
        },
        "/v1/personal-context/read": {
            "source_id": source_id,
            "source_type": "media",
            "title": "Deep Learning Fundamentals",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "category": "media_segment",
            "start_time_seconds": 125.42,
            "end_time_seconds": 0,
            "content": "# Media: Deep Learning Fundamentals",
        },
    }

    class _RoutingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: Any) -> _Response:
            path = "/" + url.split("/", 3)[3]
            return _Response(fractional_payloads[path])

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *, timeout: _RoutingClient())
    tool = module.Tools()
    tool.valves.hmac_secret = "tool-test-secret"
    user = {"id": "user-1"}

    search_out = asyncio.run(tool.search_personal_context("backprop", __user__=user))
    assert "media/Deep Learning Fundamentals @ 02:05-04:00" in search_out

    read_out = asyncio.run(tool.read_personal_context(source_id, __user__=user))
    assert f"Media source {source_id}:" in read_out
    assert "Media: media/Deep Learning Fundamentals @ 02:05" in read_out


def test_get_media_details_renders_full_breakdown(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    payloads = {
        "/v1/personal-context/media-detail": {
            "found": True,
            "url": "https://www.youtube.com/watch?v=abc12345678",
            "media_id": "00000000-0000-0000-0000-000000000001",
            "title": "Real Video Title",
            "channel_or_author": "Real Channel",
            "duration_seconds": 540,
            "summary": "Actual summary.",
            "key_takeaways": ["Fact one"],
            "topics": ["testing"],
            "segments": [
                {
                    "segment_index": 0,
                    "start_time_seconds": 0,
                    "end_time_seconds": 300.5,
                    "label": "Intro",
                    "content": "First spoken span.",
                },
                {
                    "segment_index": 1,
                    "start_time_seconds": 300,
                    "end_time_seconds": 540,
                    "label": None,
                    "content": "Second spoken span.",
                },
            ],
        }
    }

    class _Client:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: Any) -> _Response:
            path = "/" + url.split("/", 3)[3]
            return _Response(payloads[path])

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *, timeout: _Client())
    tool = module.Tools()
    tool.valves.hmac_secret = "tool-test-secret"

    out = asyncio.run(
        tool.get_media_details("https://youtu.be/abc12345678?si=z", __user__={"id": "user-1"})
    )
    assert "Indexed Media: Real Video Title" in out
    assert "Channel/Author: Real Channel" in out
    assert "Duration: 09:00" in out
    assert "[00:00-05:00] Intro:\nFirst spoken span." in out
    assert "[05:00-09:00]\nSecond spoken span." in out
    assert "First spoken span." in out

    payloads["/v1/personal-context/media-detail"] = {
        "found": False,
        "url": "https://www.youtube.com/watch?v=abc12345678",
        "media_id": None,
        "title": None,
        "channel_or_author": None,
        "duration_seconds": None,
        "summary": None,
        "key_takeaways": [],
        "topics": [],
        "segments": [],
    }
    missing = asyncio.run(
        tool.get_media_details("https://www.youtube.com/watch?v=abc12345678", __user__={"id": "user-1"})
    )
    assert "not indexed yet" in missing
    assert "process_media_url" in missing
