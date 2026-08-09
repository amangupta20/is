"""Contract tests for the self-contained Assistant Core status Tool."""

import asyncio
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from typing import Any

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
