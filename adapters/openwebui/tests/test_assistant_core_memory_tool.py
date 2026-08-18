"""Contract tests for the standalone Assistant Core Memory & Preferences Tool."""

import asyncio
import importlib.util
import json
import uuid
from pathlib import Path
from typing import Any

import pytest


def _module():
    path = Path(__file__).parents[1] / "assistant_core_memory_tool.py"
    if not path.exists():
        pytest.fail("Assistant Core Memory Tool module is missing")
    spec = importlib.util.spec_from_file_location("assistant_core_memory_tool", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, response_json: object, status_code: int = 200) -> None:
        self.response_json = response_json
        self.status_code = status_code

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


def test_save_memory_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.assistant_core_url = "http://assistant-core:8080"

    mem_id = str(uuid.uuid4())
    fake_response = {
        "created": True,
        "message": "Memory saved successfully",
        "memory": {
            "id": mem_id,
            "key": "career.job_application.datazip",
            "category": "career",
            "statement": "User applied for a software role at Datazip.",
            "temporal_tag": "job_application",
            "expires_at": "2026-10-01T00:00:00Z",
            "created_at": "2026-08-18T12:00:00Z",
            "updated_at": "2026-08-18T12:00:00Z",
        },
    }

    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout: _RecordingClient(capture, timeout, fake_response),
    )

    result = asyncio.run(
        tools.save_memory(
            key="career.job_application.datazip",
            statement="User applied for a software role at Datazip.",
            category="career",
            temporal_tag="job_application",
            expires_at="2026-10-01T00:00:00Z",
            __user__={"id": "user-42"},
        )
    )

    assert "✅ Saved new memory `career.job_application.datazip`" in result
    assert "career" in result
    assert capture["url"] == "http://assistant-core:8080/v1/personal-context/memory/save"
    sent_body = json.loads(capture["content"])
    assert sent_body["native_user_id"] == "user-42"
    assert sent_body["key"] == "career.job_application.datazip"
    assert sent_body["category"] == "career"
    assert "x-assistant-signature" in capture["headers"]


def test_update_memory_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.assistant_core_url = "http://assistant-core:8080"

    mem_id = str(uuid.uuid4())
    fake_response = {
        "message": "Memory updated",
        "memory": {
            "id": mem_id,
            "key": "infra.supabase",
            "category": "infrastructure",
            "statement": "Supabase runs on Dokploy with automated daily S3 backups.",
            "temporal_tag": None,
            "expires_at": None,
            "created_at": "2026-08-18T12:00:00Z",
            "updated_at": "2026-08-18T12:05:00Z",
        },
    }

    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout: _RecordingClient(capture, timeout, fake_response),
    )

    result = asyncio.run(
        tools.update_memory(
            memory_id=mem_id,
            statement="Supabase runs on Dokploy with automated daily S3 backups.",
            category="infrastructure",
            clear_expiration=True,
            __user__={"id": "user-42"},
        )
    )

    assert f"✅ Updated memory `infra.supabase` (ID: `{mem_id}`)" in result
    assert "infrastructure" in result
    assert capture["url"] == "http://assistant-core:8080/v1/personal-context/memory/update"
    sent_body = json.loads(capture["content"])
    assert sent_body["memory_id"] == mem_id
    assert sent_body["clear_expiration"] is True


def test_forget_memory_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.assistant_core_url = "http://assistant-core:8080"

    mem_id = str(uuid.uuid4())
    fake_response = {
        "archived_count": 1,
        "archived_ids": [mem_id],
        "message": "Archived 1 memory record(s)",
    }

    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout: _RecordingClient(capture, timeout, fake_response),
    )

    result = asyncio.run(
        tools.forget_memory(
            memory_id=mem_id,
            reason="Project was deprecated",
            __user__={"id": "user-42"},
        )
    )

    assert f"🗑️ Forgotten / archived 1 memory matching ID `{mem_id}` (Reason: Project was deprecated)." in result
    assert capture["url"] == "http://assistant-core:8080/v1/personal-context/memory/forget"


def test_list_memories_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.assistant_core_url = "http://assistant-core:8080"

    mem_id_1 = str(uuid.uuid4())
    mem_id_2 = str(uuid.uuid4())
    fake_response = {
        "total": 2,
        "memories": [
            {
                "id": mem_id_1,
                "key": "career.job_application.datazip",
                "category": "career",
                "statement": "Applied for Datazip role",
                "temporal_tag": "job_application",
                "expires_at": "2026-10-01T00:00:00Z",
                "created_at": "2026-08-18T12:00:00Z",
                "updated_at": "2026-08-18T12:00:00Z",
            },
            {
                "id": mem_id_2,
                "key": "preferences.theme",
                "category": "preference",
                "statement": "User prefers dark mode",
                "temporal_tag": None,
                "expires_at": None,
                "created_at": "2026-08-18T12:00:00Z",
                "updated_at": "2026-08-18T12:00:00Z",
            },
        ],
    }

    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout: _RecordingClient(capture, timeout, fake_response),
    )

    result = asyncio.run(
        tools.list_memories(
            query="datazip",
            category="career",
            limit=5,
            __user__={"id": "user-42"},
        )
    )

    assert "🧠 Active User Memories (2 found):" in result
    assert "career.job_application.datazip" in result
    assert "Applied for Datazip role" in result
    assert capture["url"] == "http://assistant-core:8080/v1/personal-context/memory/list"
