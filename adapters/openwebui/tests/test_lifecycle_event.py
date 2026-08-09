"""Contract tests for the self-contained Open WebUI lifecycle Event Function."""

import asyncio
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError


def _module():
    path = Path(__file__).parents[1] / "lifecycle_event.py"
    if not path.exists():
        pytest.fail("lifecycle Event module is missing")
    spec = importlib.util.spec_from_file_location("lifecycle_event", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def raise_for_status(self) -> None:
        return None


class _RecordingClient:
    def __init__(self, capture: dict[str, Any], timeout: float) -> None:
        capture["timeout"] = timeout
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self.capture["url"] = url
        self.capture.update(kwargs)
        return _Response()


def test_allowlisted_event_forwards_normalized_metadata_and_exact_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient(capture, timeout),
    )
    monkeypatch.setattr(module.time, "time", lambda: 1_800_000_000)
    adapter = module.Event()
    adapter.valves.hmac_secret = "event-test-secret"

    asyncio.run(
        adapter.event(
            {
                "actor": {"id": 42, "display_name": "must not leak"},
                "user": {"id": "fallback-user"},
                "chat": {"id": 123, "title": "private title", "messages": ["private"]},
                "message": {"id": 456, "content": "private content"},
                "file": {"id": 789, "filename": "private.pdf", "path": "/private"},
                "unknown": {"prompt": "private prompt"},
            },
            __event_id__="stable-event-id",
            __event_name__="file.uploaded",
            __irrelevant__="accepted",
        )
    )

    assert capture["timeout"] == 2.0
    assert capture["url"] == "http://assistant-core:8080/v1/events"
    body = capture["content"]
    envelope = json.loads(body)
    assert envelope == {
        "schema_version": 1,
        "event_id": "stable-event-id",
        "event_type": "file.uploaded",
        "occurred_at": envelope["occurred_at"],
        "native_user_id": "42",
        "native_chat_id": "123",
        "native_message_id": "456",
        "payload": {"file_id": "789", "source": "openwebui_event"},
    }
    assert envelope["occurred_at"].endswith("+00:00")
    assert body == json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()

    headers = capture["headers"]
    assert headers["x-assistant-timestamp"] == "1800000000"
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"POST\n/v1/events\n1800000000\n{digest}".encode()
    expected = hmac.new(b"event-test-secret", canonical, hashlib.sha256).hexdigest()
    assert headers == {
        "content-type": "application/json",
        "x-assistant-timestamp": "1800000000",
        "x-assistant-signature": expected,
    }


@pytest.mark.parametrize(
    "event_name",
    [
        "chat.finished",
        "chat.deleted",
        "chat.compacted",
        "message.created",
        "file.uploaded",
        "file.deleted",
        "user.deleted",
    ],
)
def test_every_allowlisted_name_is_forwarded(
    event_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient(capture, timeout),
    )

    asyncio.run(
        module.Event().event(
            {"user": {"id": "user-id"}},
            __event_id__="event-id",
            __event_name__=event_name,
        )
    )

    assert json.loads(capture["content"])["event_type"] == event_name


@pytest.mark.parametrize(
    ("event", "event_id", "event_name"),
    [
        ({"user": {"id": "user"}}, "event", "chat.started"),
        ({"user": {"id": "user"}}, None, "chat.finished"),
        ("not-a-mapping", "event", "chat.finished"),
        ({"chat": {"id": "chat"}}, "event", "chat.finished"),
        ({"actor": {}, "user": {}}, "event", "chat.finished"),
    ],
)
def test_irrelevant_or_unidentifiable_events_are_ignored(
    event: object,
    event_id: str | None,
    event_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()

    def unexpected_client(**_kwargs: object) -> None:
        pytest.fail("ignored event attempted delivery")

    monkeypatch.setattr(module.httpx, "AsyncClient", unexpected_client)

    asyncio.run(module.Event().event(event, __event_id__=event_id, __event_name__=event_name))


def test_delivery_failure_is_bounded_payload_free_and_fail_open(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()

    class _FailingClient(_RecordingClient):
        async def post(self, url: str, **kwargs: Any) -> _Response:
            raise RuntimeError("SECRET response body and URL must never appear")

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _FailingClient({}, timeout),
    )
    long_name = "chat.finished" + "x" * 200
    long_id = "event-id-" + "y" * 200

    asyncio.run(
        module.Event().event(
            {"user": {"id": "user"}, "message": {"content": "PRIVATE"}},
            __event_id__=long_id,
            __event_name__=long_name,
        )
    )

    diagnostic = capsys.readouterr().err.strip()
    assert diagnostic == ""

    asyncio.run(
        module.Event().event(
            {"user": {"id": "user"}, "message": {"content": "PRIVATE"}},
            __event_id__=long_id,
            __event_name__="chat.finished",
        )
    )
    diagnostic = capsys.readouterr().err.strip()
    parsed = json.loads(diagnostic)
    assert parsed["failure"] == "assistant_core_event_delivery_failed"
    assert parsed["event_name"] == "chat.finished"
    assert parsed["event_id"] == long_id[:80]
    assert set(parsed) == {"failure", "event_name", "event_id"}
    assert "SECRET" not in diagnostic
    assert "PRIVATE" not in diagnostic


def test_event_valves_are_json_persistable_and_password_marked() -> None:
    module = _module()
    valves = module.Event.Valves()

    assert json.loads(valves.model_dump_json()) == {
        "assistant_core_url": "http://assistant-core:8080",
        "hmac_secret": "development-hmac-secret-change-me",
        "timeout_seconds": 2.0,
    }
    schema = module.Event.Valves.model_json_schema()
    assert schema["properties"]["hmac_secret"]["input"] == {"type": "password"}
    with pytest.raises(ValidationError):
        module.Event.Valves(timeout_seconds=0.09)
    with pytest.raises(ValidationError):
        module.Event.Valves(timeout_seconds=10.01)
