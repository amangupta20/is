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


class _RaisingGetMapping(dict[str, object]):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def get(self, key: str, default: object = None) -> object:
        raise self.failure


class _RaisingString:
    def __str__(self) -> str:
        raise RuntimeError("unsafe identifier conversion")


def _forward_current_event(
    monkeypatch: pytest.MonkeyPatch,
    event: dict[str, object],
    event_name: str,
) -> dict[str, Any]:
    module = _module()
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _RecordingClient(capture, timeout),
    )

    asyncio.run(
        module.Event().event(
            event,
            __event_id__="current-envelope-event",
            __event_name__=event_name,
        )
    )

    return json.loads(capture["content"])


@pytest.mark.parametrize(
    ("event_name", "event", "expected"),
    [
        (
            "message.created",
            {
                "actor": {"id": "actor-user"},
                "subject": {"id": "message-current"},
                "data": {"chat_id": "chat-current", "content": "PRIVATE"},
            },
            ("actor-user", "chat-current", "message-current", None),
        ),
        (
            "chat.finished",
            {
                "actor": {"id": "actor-user"},
                "subject": {"id": "chat-current"},
                "data": {"message_id": "message-current", "user_id": "data-user"},
            },
            ("actor-user", "chat-current", "message-current", None),
        ),
        (
            "chat.deleted",
            {
                "actor": {"id": "actor-user"},
                "subject": {"id": "chat-current"},
                "data": {"owner_id": "owner-current"},
            },
            ("owner-current", "chat-current", None, None),
        ),
        (
            "chat.compacted",
            {"actor": {"id": "actor-user"}, "subject": {"id": "chat-current"}},
            ("actor-user", "chat-current", None, None),
        ),
        (
            "file.uploaded",
            {"actor": {"id": "actor-user"}, "subject": {"id": "file-current"}},
            ("actor-user", None, None, "file-current"),
        ),
        (
            "file.deleted",
            {"actor": {"id": "actor-user"}, "subject": {"id": "file-current"}},
            ("actor-user", None, None, "file-current"),
        ),
        (
            "user.deleted",
            {"actor": {"id": "deleting-admin"}, "subject": {"id": "deleted-user"}},
            ("deleted-user", None, None, None),
        ),
    ],
)
def test_current_envelope_extracts_only_allowlisted_identifier_fields(
    event_name: str,
    event: dict[str, object],
    expected: tuple[str, str | None, str | None, str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = _forward_current_event(monkeypatch, event, event_name)

    user_id, chat_id, message_id, file_id = expected
    assert envelope["native_user_id"] == user_id
    assert envelope["native_chat_id"] == chat_id
    assert envelope["native_message_id"] == message_id
    assert envelope["payload"] == {
        "source": "openwebui_event",
        **({"file_id": file_id} if file_id is not None else {}),
    }
    assert "PRIVATE" not in json.dumps(envelope)


def test_actorless_user_deleted_uses_subject_as_the_deleted_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = _forward_current_event(
        monkeypatch,
        {"subject": {"id": "deleted-user"}},
        "user.deleted",
    )

    assert envelope["native_user_id"] == "deleted-user"


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
def test_legacy_nested_identifiers_remain_supported(
    event_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    envelope = _forward_current_event(
        monkeypatch,
        {
            "user": {"id": "legacy-user"},
            "chat": {"id": "legacy-chat"},
            "message": {"id": "legacy-message"},
            "file": {"id": "legacy-file"},
        },
        event_name,
    )

    assert envelope["native_user_id"] == "legacy-user"
    assert envelope["native_chat_id"] == "legacy-chat"
    assert envelope["native_message_id"] == "legacy-message"
    assert envelope["payload"] == {"file_id": "legacy-file", "source": "openwebui_event"}


def test_chat_finished_uses_data_user_id_when_actor_and_legacy_user_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = _forward_current_event(
        monkeypatch,
        {
            "subject": {"id": "chat-current"},
            "data": {"message_id": "message-current", "user_id": "data-user"},
        },
        "chat.finished",
    )

    assert envelope["native_user_id"] == "data-user"


@pytest.mark.parametrize(
    "malformed_identifier",
    [{"nested": "not-an-id"}, ["not-an-id"], _RaisingString()],
)
def test_malformed_scalar_identifiers_are_ignored_without_delivery(
    malformed_identifier: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()

    def unexpected_client(**_kwargs: object) -> None:
        pytest.fail("malformed identifier attempted delivery")

    monkeypatch.setattr(module.httpx, "AsyncClient", unexpected_client)

    asyncio.run(
        module.Event().event(
            {"actor": {"id": malformed_identifier}, "subject": {"id": "chat"}},
            __event_id__="event",
            __event_name__="chat.finished",
        )
    )


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


def test_unhashable_event_name_is_ignored_without_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()

    def unexpected_client(**_kwargs: object) -> None:
        pytest.fail("invalid event name attempted delivery")

    monkeypatch.setattr(module.httpx, "AsyncClient", unexpected_client)

    asyncio.run(
        module.Event().event(
            {"user": {"id": "user"}},
            __event_id__="event",
            __event_name__=["chat.finished"],
        )
    )


def test_mapping_access_exception_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()

    def unexpected_client(**_kwargs: object) -> None:
        pytest.fail("malformed mapping attempted delivery")

    monkeypatch.setattr(module.httpx, "AsyncClient", unexpected_client)

    asyncio.run(
        module.Event().event(
            _RaisingGetMapping(RuntimeError("unsafe mapping access")),
            __event_id__="event",
            __event_name__="chat.finished",
        )
    )


def test_identifier_string_conversion_exception_is_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()

    def unexpected_client(**_kwargs: object) -> None:
        pytest.fail("malformed identifier attempted delivery")

    monkeypatch.setattr(module.httpx, "AsyncClient", unexpected_client)

    asyncio.run(
        module.Event().event(
            {"actor": {"id": _RaisingString()}},
            __event_id__="event",
            __event_name__="chat.finished",
        )
    )


def test_diagnostic_output_exception_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()

    class _FailingClient(_RecordingClient):
        async def post(self, url: str, **kwargs: Any) -> _Response:
            raise RuntimeError("delivery failed")

    def fail_output(*_args: object, **_kwargs: object) -> None:
        raise OSError("diagnostic output failed")

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _FailingClient({}, timeout),
    )
    monkeypatch.setattr(module, "print", fail_output, raising=False)

    asyncio.run(
        module.Event().event(
            {"user": {"id": "user"}},
            __event_id__="event",
            __event_name__="chat.finished",
        )
    )


def test_mapping_access_baseexception_still_propagates() -> None:
    module = _module()

    with pytest.raises(SystemExit):
        asyncio.run(
            module.Event().event(
                _RaisingGetMapping(SystemExit()),
                __event_id__="event",
                __event_name__="chat.finished",
            )
        )


def test_delivery_cancellation_still_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()

    class _CancelledClient(_RecordingClient):
        async def post(self, url: str, **kwargs: Any) -> _Response:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *, timeout: _CancelledClient({}, timeout),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            module.Event().event(
                {"user": {"id": "user"}},
                __event_id__="event",
                __event_name__="chat.finished",
            )
        )


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
