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
    def __init__(
        self, capture: dict[str, Any], timeout: float, response_json: object
    ) -> None:
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
            "sources": [
                {"source_type": "memory", "source_id": source_id, "label": "profile"}
            ],
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
                {
                    "source_id": "00000000-0000-0000-0000-000000000013",
                    "source_type": "file",
                    "category": "file_evidence",
                    "role": None,
                    "preview": "Architecture specification notes.",
                    "source_native_chat_id": "file-abc",
                    "source_native_message_id": "0",
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
        "<user_profile>\n- Use direct answers.\n</user_profile>\n"
        f"Profile source IDs: {source_id}."
    )
    assert asyncio.run(
        tool.search_personal_context("direct", __user__=user, __metadata__=metadata)
    ) == (
        "Personal context search (hybrid):\n"
        f"- [memory/preference] {source_id}: Use direct answers.\n"
        "- [conversation/assistant evidence] 00000000-0000-0000-0000-000000000012: "
        "We used bounded hybrid recall. (chat chat-2, message message-2)\n"
        "- [file evidence] 00000000-0000-0000-0000-000000000013: "
        "Architecture specification notes. (file file-abc, chunk 0)"
    )
    assert asyncio.run(
        tool.read_personal_context(source_id, __user__=user)
    ) == (
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


def test_tool_list_memories_formats_markdown_and_handles_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    memories_payload = {
        "memories": [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "key": "pref_1",
                "category": "preference",
                "statement": "Keep responses concise.",
                "state": "active",
                "confidence": 95,
                "created_at": "2026-08-15T03:00:00Z",
                "updated_at": "2026-08-15T03:00:00Z",
                "evidence_quote": "Please keep it brief.",
            },
            {
                "id": "00000000-0000-0000-0000-000000000002",
                "key": "fact_1",
                "category": "fact",
                "statement": "Building Open WebUI companion.",
                "state": "active",
                "confidence": 90,
                "created_at": "2026-08-14T12:00:00Z",
                "updated_at": "2026-08-14T12:00:00Z",
                "evidence_quote": None,
            },
        ]
    }
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient(capture, timeout, memories_payload),
    )
    tool = module.Tools()
    user = {"id": "user-1"}
    res = asyncio.run(
        tool.list_memories(category="preference", status="active", limit=50, __user__=user)
    )

    assert (
        "- [preference] 00000000-0000-0000-0000-000000000001: Keep responses concise. (created 2026-08-15 | state: active)\n  Evidence: \"Please keep it brief.\""
        in res
    )
    assert (
        "- [fact] 00000000-0000-0000-0000-000000000002: Building Open WebUI companion. (created 2026-08-14 | state: active)"
        in res
    )

    body = json.loads(capture["content"])
    assert body["native_user_id"] == "user-1"
    assert body["category"] == "preference"
    assert body["status"] == "active"
    assert body["limit"] == 50

    # Test empty list
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient(capture, timeout, {"memories": []}),
    )
    empty_res = asyncio.run(tool.list_memories(__user__=user))
    assert empty_res == "No memories found matching criteria."

    # Test invalid status fallback to active and category None
    asyncio.run(
        tool.list_memories(category="", status="invalid_status", limit=20, __user__=user)
    )
    body_fallback = json.loads(capture["content"])
    assert body_fallback["status"] == "active"
    assert body_fallback["category"] is None
    assert body_fallback["limit"] == 20

    # Test failure / network error fails open to UNAVAILABLE
    class _FailingClient(_RecordingClient):
        async def post(self, url: str, **kwargs: Any) -> _Response:
            raise RuntimeError("network failure")

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _FailingClient({}, timeout, {}),
    )
    assert asyncio.run(tool.list_memories(__user__=user)) == UNAVAILABLE


def test_tool_archive_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    mem_id = "00000000-0000-0000-0000-000000000001"
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient(
            capture,
            timeout,
            {"status": "ok", "archived_id": mem_id, "archived_at": "2026-08-15T03:30:00Z"},
        ),
    )
    tool = module.Tools()
    user = {"id": "user-1"}
    res = asyncio.run(tool.archive_memory(memory_id=f"  {mem_id}  ", __user__=user))
    assert res == f"Successfully archived memory   {mem_id}  ."
    body = json.loads(capture["content"])
    assert body["native_user_id"] == "user-1"
    assert body["memory_id"] == mem_id

    # Test empty memory_id fails open to UNAVAILABLE
    assert asyncio.run(tool.archive_memory(memory_id="   ", __user__=user)) == UNAVAILABLE

    # Test failure / network error fails open to UNAVAILABLE
    class _FailingClient(_RecordingClient):
        async def post(self, url: str, **kwargs: Any) -> _Response:
            raise RuntimeError("network failure")

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _FailingClient({}, timeout, {}),
    )
    assert asyncio.run(tool.archive_memory(memory_id=mem_id, __user__=user)) == UNAVAILABLE


def test_tool_merge_memories(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    source_ids = "00000000-0000-0000-0000-000000000001, 00000000-0000-0000-0000-000000000002"
    created_id = "00000000-0000-0000-0000-000000000003"
    new_stmt = "Consolidated preference statement."
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient(
            capture,
            timeout,
            {
                "status": "ok",
                "created_id": created_id,
                "archived_source_ids": [
                    "00000000-0000-0000-0000-000000000001",
                    "00000000-0000-0000-0000-000000000002",
                ],
            },
        ),
    )
    tool = module.Tools()
    user = {"id": "user-1"}
    res = asyncio.run(
        tool.merge_memories(
            source_memory_ids=source_ids,
            new_statement=new_stmt,
            category="preference",
            __user__=user,
        )
    )
    assert (
        res
        == f"Successfully merged 2 memories into new preference memory {created_id}: '{new_stmt}'."
    )
    body = json.loads(capture["content"])
    assert body["native_user_id"] == "user-1"
    assert body["source_memory_ids"] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert body["target_category"] == "preference"
    assert body["new_statement"] == new_stmt

    # Test fewer than 2 IDs fails open to UNAVAILABLE
    assert (
        asyncio.run(
            tool.merge_memories(
                source_memory_ids="00000000-0000-0000-0000-000000000001",
                new_statement=new_stmt,
                category="preference",
                __user__=user,
            )
        )
        == UNAVAILABLE
    )

    # Test empty statement fails open to UNAVAILABLE
    assert (
        asyncio.run(
            tool.merge_memories(
                source_memory_ids=source_ids,
                new_statement="   ",
                category="preference",
                __user__=user,
            )
        )
        == UNAVAILABLE
    )

    # Test failure / network error fails open to UNAVAILABLE
    class _FailingClient(_RecordingClient):
        async def post(self, url: str, **kwargs: Any) -> _Response:
            raise RuntimeError("network failure")

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _FailingClient({}, timeout, {}),
    )
    assert (
        asyncio.run(
            tool.merge_memories(
                source_memory_ids=source_ids,
                new_statement=new_stmt,
                category="preference",
                __user__=user,
            )
        )
        == UNAVAILABLE
    )

