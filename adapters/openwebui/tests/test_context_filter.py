"""Behaviour tests for the standalone Assistant Context Filter."""

import asyncio
import copy
import hashlib
import hmac
import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx
import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
context_filter = importlib.import_module("context_filter")
Filter = context_filter.Filter


def test_filter_valves_are_json_persistable_and_mark_the_secret_as_password() -> None:
    """Open WebUI can persist Valve values while rendering the secret as a password."""
    filter_ = Filter()
    filter_.valves = Filter.Valves(hmac_secret="persistable-secret")

    persisted = json.dumps(filter_.valves.model_dump(exclude_unset=True))
    schema = Filter.Valves.model_json_schema()

    assert json.loads(persisted) == {"hmac_secret": "persistable-secret"}
    assert schema["properties"]["hmac_secret"]["input"] == {"type": "password"}


def test_filter_valves_expose_a_string_budget_above_the_numeric_widget_cap() -> None:
    """The persisted budget is text so Open WebUI does not cap it at 9,999."""
    schema = Filter.Valves.model_json_schema()

    assert Filter.Valves().max_context_tokens == "300000"
    assert schema["properties"]["max_context_tokens"]["type"] == "string"
    assert Filter.Valves(max_context_tokens="500000").max_context_tokens == "500000"


def test_filter_valves_normalize_legacy_integer_budgets() -> None:
    """Previously persisted integer Valve values remain compatible."""
    assert Filter.Valves(max_context_tokens=0).max_context_tokens == "0"
    assert Filter.Valves(max_context_tokens=500_000).max_context_tokens == "500000"


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        1.0,
        -1,
        500_001,
        "",
        " ",
        " 1",
        "1 ",
        "+1",
        "-1",
        "01",
        "000000",
        "1.0",
        "1,000",
        "500001",
        "١",
        "１２３",
    ],
)
def test_filter_valves_reject_noncanonical_or_out_of_range_budgets(value: object) -> None:
    """Only canonical decimal strings and legacy plain integers are accepted."""
    with pytest.raises(ValidationError):
        Filter.Valves(max_context_tokens=value)


def test_empty_context_leaves_the_native_body_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """An intentionally empty response does not alter the native prompt when temporal anchor is off."""
    filter_ = Filter()
    filter_.valves.inject_temporal_anchor = False
    body = {"messages": [{"role": "user", "content": "hello"}]}
    original = copy.deepcopy(body)

    async def empty_context(_: dict) -> dict:
        return {"context_text": "", "token_estimate": 0, "sources": [], "degraded": False}

    monkeypatch.setattr(filter_, "_post_context", empty_context)

    assert anyio.run(lambda: filter_.inlet(body, __user__={"id": "u"})) == original


def test_timeout_leaves_the_native_body_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded companion timeout is invisible to the native chat request when temporal anchor is off."""
    filter_ = Filter()
    filter_.valves.inject_temporal_anchor = False
    body = {"messages": [{"role": "user", "content": "hello"}]}
    original = copy.deepcopy(body)

    async def timeout(_: dict) -> dict:
        raise httpx.ReadTimeout("bounded timeout")

    monkeypatch.setattr(filter_, "_post_context", timeout)

    assert anyio.run(lambda: filter_.inlet(body, __user__={"id": "u"})) == original


def test_malformed_response_leaves_the_native_body_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful but invalid companion payload is also fail-open."""
    filter_ = Filter()
    filter_.valves.inject_temporal_anchor = False
    body = {"messages": [{"role": "user", "content": "hello"}]}
    original = copy.deepcopy(body)

    async def malformed(_: dict) -> dict:
        return {"not_context_text": "missing expected field"}

    monkeypatch.setattr(filter_, "_post_context", malformed)

    assert anyio.run(lambda: filter_.inlet(body, __user__={"id": "u"})) == original


def test_filter_signs_exact_compact_sorted_bytes_sent_to_companion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The companion receives byte-for-byte the payload covered by the HMAC."""
    secret = "test-signing-secret"
    filter_ = Filter()
    filter_.valves = Filter.Valves(
        assistant_core_url="http://companion.test",
        hmac_secret=secret,
        inject_temporal_anchor=False,
    )
    expected_payload = {
        "native_user_id": "u-1",
        "native_chat_id": "chat-1",
        "native_message_id": "message-1",
        "request_text": "x" * 16_000,
        "max_tokens": 300_000,
    }

    async def companion(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/context"
        expected_body = json.dumps(expected_payload, separators=(",", ":"), sort_keys=True).encode()
        assert request.content == expected_body
        timestamp = request.headers["x-assistant-timestamp"]
        digest = hashlib.sha256(request.content).hexdigest()
        canonical = f"POST\n/v1/context\n{timestamp}\n{digest}".encode()
        expected_signature = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
        assert request.headers["x-assistant-signature"] == expected_signature
        return httpx.Response(200, json={"context_text": ""})

    transport = httpx.MockTransport(companion)
    real_client = httpx.AsyncClient

    def mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return real_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(context_filter.httpx, "AsyncClient", mock_client)
    body = {"messages": [{"role": "user", "content": "x" * 16_001}]}
    original = copy.deepcopy(body)

    assert (
        anyio.run(
            lambda: filter_.inlet(
                body,
                __user__={"id": "u-1"},
                __metadata__={"chat_id": "chat-1", "message_id": "message-1"},
            )
        )
        == original
    )


def test_inlet_injects_temporal_anchor_in_configured_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inlet automatically injects real-world datetime anchor in user's IST timezone."""
    filter_ = Filter()
    filter_.valves = Filter.Valves(
        user_timezone="Asia/Kolkata",
        inject_temporal_anchor=True,
    )
    body = {
        "messages": [
            {"role": "user", "content": "I am working on problem 4 today."},
        ]
    }

    async def empty_context(_: dict) -> dict:
        return {"context_text": ""}

    monkeypatch.setattr(filter_, "_post_context", empty_context)

    result = anyio.run(lambda: filter_.inlet(body, __user__={"id": "u-1"}))
    assert len(result["messages"]) == 2
    system_msg = result["messages"][0]
    assert system_msg["role"] == "system"
    assert "<assistant_context>" in system_msg["content"]
    assert "<current_datetime>" in system_msg["content"]
    assert "Asia/Kolkata" in system_msg["content"]
    assert "IST" in system_msg["content"]
    assert "Temporal Grounding:" in system_msg["content"]


def test_nonempty_context_is_inserted_at_the_fixed_system_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frozen context follows leading system policy and precedes conversation history."""
    filter_ = Filter()
    filter_.valves.inject_temporal_anchor = False
    body = {
        "messages": [
            {"role": "system", "content": "Native policy"},
            {"role": "system", "content": "Stable tool policy"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Latest request"},
            {"role": "assistant", "content": "Generated continuation"},
        ]
    }

    async def nonempty_context(_: dict) -> dict:
        return {"context_text": "Preferred response style: direct."}

    monkeypatch.setattr(filter_, "_post_context", nonempty_context)

    result = anyio.run(lambda: filter_.inlet(body, __user__={"id": "u"}))

    assert result["messages"] == [
        {"role": "system", "content": "Native policy"},
        {"role": "system", "content": "Stable tool policy"},
        {
            "role": "system",
            "content": "<assistant_context>\nPreferred response style: direct.\n</assistant_context>",
        },
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Latest request"},
        {"role": "assistant", "content": "Generated continuation"},
    ]


def test_outlet_selects_current_pair_by_stable_ids_and_forwards_only_visible_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Misleading branch order and private fields cannot change the selected pair."""
    filter_ = Filter()
    sent: dict[str, object] = {}

    async def record_event(path: str, payload: dict[str, object]) -> dict[str, object]:
        sent["path"] = path
        sent["payload"] = payload
        return {"event_id": payload["event_id"], "duplicate": False}

    monkeypatch.setattr(filter_, "_post_signed", record_event, raising=False)
    body = {
        "chat_id": "chat-current",
        "id": "assistant-current",
        "model": "private-model",
        "messages": [
            {"id": "system-old", "role": "system", "content": "PRIVATE_SYSTEM"},
            {"id": "user-old", "role": "user", "content": "old question"},
            {"id": "assistant-old", "role": "assistant", "content": "old answer"},
            {"id": "user-current", "role": "user", "content": "visible question", "timestamp": 7},
            {"id": "tool-tail", "role": "tool", "content": "PRIVATE_TOOL"},
            {
                "id": "assistant-current",
                "role": "assistant",
                "content": "visible answer",
                "timestamp": 8,
                "output": {"secret": "PRIVATE_OUTPUT"},
                "sources": [{"url": "https://private.invalid/source"}],
                "usage": {"private": "PRIVATE_USAGE"},
            },
            {"id": "user-tail", "role": "user", "content": "misleading tail"},
        ],
        "files": [{"name": "PRIVATE_FILENAME"}],
    }
    user = {"id": "user-current-owner", "email": "PRIVATE_EMAIL"}
    metadata = {
        "user_id": "user-current-owner",
        "chat_id": "chat-current",
        "message_id": "assistant-current",
        "user_message_id": "user-current",
        "assistant_message_id": "assistant-being-continued",
        "private": "PRIVATE_METADATA",
    }

    result = anyio.run(filter_.outlet, body, user, metadata)

    assert result is body
    assert sent["path"] == "/v1/events"
    envelope = sent["payload"]
    assert isinstance(envelope, dict)
    assert envelope["event_type"] == "turn.completed.v1"
    assert envelope["native_user_id"] == "user-current-owner"
    assert envelope["native_chat_id"] == "chat-current"
    assert envelope["native_message_id"] == "assistant-current"
    assert envelope["payload"] == {
        "source": "openwebui_outlet_filter",
        "user_message": {
            "id": "user-current",
            "role": "user",
            "content": "visible question",
            "sha256": hashlib.sha256(b"visible question").hexdigest(),
            "timestamp": 7,
        },
        "assistant_message": {
            "id": "assistant-current",
            "role": "assistant",
            "content": "visible answer",
            "sha256": hashlib.sha256(b"visible answer").hexdigest(),
            "timestamp": 8,
        },
    }
    serialized = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    for private in (
        "PRIVATE_SYSTEM",
        "PRIVATE_TOOL",
        "PRIVATE_OUTPUT",
        "PRIVATE_USAGE",
        "PRIVATE_FILENAME",
        "PRIVATE_EMAIL",
        "PRIVATE_METADATA",
        "https://private.invalid/source",
        "misleading tail",
    ):
        assert private not in serialized


class _FixedDatetime:
    @classmethod
    def now(cls, timezone: object) -> datetime:
        assert timezone is UTC
        return datetime(2026, 8, 10, 12, 34, 56, 123456, tzinfo=UTC)


def _outlet_fixture(
    *,
    user_content: str = "visible question",
    assistant_content: str = "visible answer",
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    body: dict[str, object] = {
        "chat_id": "chat-current",
        "id": "assistant-current",
        "messages": [
            {
                "id": "user-current",
                "role": "user",
                "content": user_content,
                "timestamp": 7,
            },
            {
                "id": "assistant-current",
                "role": "assistant",
                "content": assistant_content,
                "timestamp": 8,
            },
        ],
    }
    user: dict[str, object] = {"id": "user-current-owner"}
    metadata: dict[str, object] = {
        "user_id": "user-current-owner",
        "chat_id": "chat-current",
        "message_id": "assistant-current",
        "user_message_id": "user-current",
    }
    return body, user, metadata


def _completed_envelope(filter_: Filter, assistant_content: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": filter_._turn_event_id(
            "user-current-owner", "chat-current", "assistant-current"
        ),
        "event_type": "turn.completed.v1",
        "occurred_at": "2026-08-10T12:34:56.123456+00:00",
        "native_user_id": "user-current-owner",
        "native_chat_id": "chat-current",
        "native_project_id": None,
        "native_folder_id": None,
        "native_message_id": "assistant-current",
        "payload": {
            "source": "openwebui_outlet_filter",
            "user_message": {
                "id": "user-current",
                "role": "user",
                "content": "visible question",
                "sha256": hashlib.sha256(b"visible question").hexdigest(),
                "timestamp": 7,
            },
            "assistant_message": {
                "id": "assistant-current",
                "role": "assistant",
                "content": assistant_content,
                "sha256": hashlib.sha256(assistant_content.encode()).hexdigest(),
                "timestamp": 8,
            },
        },
    }


def test_outlet_switches_only_after_exact_512_kib_utf8_event_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signed UTF-8 envelope is exact at the cap and metadata-only above it."""
    filter_ = Filter()
    monkeypatch.setattr(context_filter, "datetime", _FixedDatetime)
    prefix = "漢"
    prefix_size = len(filter_._event_bytes(_completed_envelope(filter_, prefix)))
    boundary_content = prefix + "x" * (524_288 - prefix_size)
    assert len(filter_._event_bytes(_completed_envelope(filter_, boundary_content))) == 524_288

    captured: list[dict[str, object]] = []

    async def record_event(_path: str, payload: dict[str, object]) -> dict[str, object]:
        captured.append(payload)
        return {"event_id": payload["event_id"], "duplicate": False}

    monkeypatch.setattr(filter_, "_post_signed", record_event)
    boundary_body, user, metadata = _outlet_fixture(assistant_content=boundary_content)
    over_body, _, _ = _outlet_fixture(assistant_content=boundary_content + "x")

    assert anyio.run(filter_.outlet, boundary_body, user, metadata) is boundary_body
    assert anyio.run(filter_.outlet, over_body, user, metadata) is over_body

    assert captured[0]["event_type"] == "turn.completed.v1"
    assert len(filter_._event_bytes(captured[0])) == 524_288
    assert captured[1]["event_type"] == "turn.oversized.v1"


def test_oversized_event_contains_only_ids_hashes_and_utf8_byte_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized markers cannot carry content or unrelated native private data."""
    filter_ = Filter()
    filter_.valves = Filter.Valves(hmac_secret="PRIVATE_HMAC_SECRET")
    monkeypatch.setattr(context_filter, "datetime", _FixedDatetime)
    private_user = "PRIVATE_USER_CONTENT_漢" + "u" * 525_000
    private_assistant = "PRIVATE_ASSISTANT_CONTENT_🙂"
    body, user, metadata = _outlet_fixture(
        user_content=private_user,
        assistant_content=private_assistant,
    )
    assert isinstance(body["messages"], list)
    body["messages"].extend(
        [
            {"id": "system", "role": "system", "content": "PRIVATE_SYSTEM"},
            {"id": "tool", "role": "tool", "content": "PRIVATE_TOOL"},
        ]
    )
    body.update(
        {
            "output": "PRIVATE_OUTPUT",
            "sources": "PRIVATE_SOURCES",
            "usage": "PRIVATE_USAGE",
            "url": "https://private.invalid/signed",
            "files": [{"name": "PRIVATE_FILENAME"}],
        }
    )
    metadata["private"] = "PRIVATE_METADATA"
    captured: dict[str, object] = {}

    async def record_event(_path: str, payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {"event_id": payload["event_id"], "duplicate": False}

    monkeypatch.setattr(filter_, "_post_signed", record_event)

    assert anyio.run(filter_.outlet, body, user, metadata) is body

    assert captured["event_type"] == "turn.oversized.v1"
    assert captured["payload"] == {
        "source": "openwebui_outlet_filter",
        "user_message": {
            "id": "user-current",
            "sha256": hashlib.sha256(private_user.encode()).hexdigest(),
            "content_bytes": len(private_user.encode()),
        },
        "assistant_message": {
            "id": "assistant-current",
            "sha256": hashlib.sha256(private_assistant.encode()).hexdigest(),
            "content_bytes": len(private_assistant.encode()),
        },
    }
    serialized = filter_._event_bytes(captured).decode()
    for private in (
        "PRIVATE_USER_CONTENT",
        "PRIVATE_ASSISTANT_CONTENT",
        "PRIVATE_SYSTEM",
        "PRIVATE_TOOL",
        "PRIVATE_OUTPUT",
        "PRIVATE_SOURCES",
        "PRIVATE_USAGE",
        "PRIVATE_METADATA",
        "PRIVATE_HMAC_SECRET",
        "PRIVATE_FILENAME",
        "https://private.invalid/signed",
    ):
        assert private not in serialized


@pytest.mark.parametrize(
    "case",
    [
        "missing_user_id",
        "missing_metadata_user_id",
        "missing_chat_id",
        "missing_metadata_chat_id",
        "missing_assistant_id",
        "missing_metadata_message_id",
        "missing_user_message_id",
        "mismatched_user",
        "mismatched_chat",
        "mismatched_assistant",
        "temporary_chat",
        "legacy_temporary_chat",
        "channel_chat",
        "duplicate_user_id",
        "duplicate_assistant_id",
        "non_string_user_content",
        "non_string_assistant_content",
        "malformed_messages",
        "boolean_timestamp",
        "float_timestamp",
        "negative_timestamp",
        "null_timestamp",
    ],
)
def test_outlet_rejects_unstable_or_malformed_native_turns_without_delivery(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incomplete, unsaved, ambiguous, or non-exact native turns are not captured."""
    filter_ = Filter()
    body, user, metadata = _outlet_fixture()

    if case == "missing_user_id":
        user.pop("id")
    elif case == "missing_metadata_user_id":
        metadata.pop("user_id")
    elif case == "missing_chat_id":
        body.pop("chat_id")
    elif case == "missing_metadata_chat_id":
        metadata.pop("chat_id")
    elif case == "missing_assistant_id":
        body.pop("id")
    elif case == "missing_metadata_message_id":
        metadata.pop("message_id")
    elif case == "missing_user_message_id":
        metadata.pop("user_message_id")
    elif case == "mismatched_user":
        metadata["user_id"] = "different-user"
    elif case == "mismatched_chat":
        metadata["chat_id"] = "different-chat"
    elif case == "mismatched_assistant":
        metadata["message_id"] = "different-assistant"
    elif case == "temporary_chat":
        body["chat_id"] = metadata["chat_id"] = "temporary:session"
    elif case == "legacy_temporary_chat":
        body["chat_id"] = metadata["chat_id"] = "local:session"
    elif case == "channel_chat":
        body["chat_id"] = metadata["chat_id"] = "channel:session"
    elif case == "duplicate_user_id":
        assert isinstance(body["messages"], list)
        body["messages"].append({"id": "user-current", "role": "user", "content": "duplicate"})
    elif case == "duplicate_assistant_id":
        assert isinstance(body["messages"], list)
        body["messages"].append(
            {"id": "assistant-current", "role": "assistant", "content": "duplicate"}
        )
    elif case == "malformed_messages":
        body["messages"] = {"not": "a list"}
    else:
        assert isinstance(body["messages"], list)
        message_index = 0 if "user" in case else 1
        message = body["messages"][message_index]
        assert isinstance(message, dict)
        if case == "non_string_user_content" or case == "non_string_assistant_content":
            message["content"] = ["not", "visible", "text"]
        elif case == "boolean_timestamp":
            message["timestamp"] = True
        elif case == "float_timestamp":
            message["timestamp"] = 1.0
        elif case == "negative_timestamp":
            message["timestamp"] = -1
        elif case == "null_timestamp":
            message["timestamp"] = None

    async def unexpected_delivery(_path: str, _payload: dict[str, object]) -> dict[str, object]:
        pytest.fail(f"{case} attempted delivery")

    monkeypatch.setattr(filter_, "_post_signed", unexpected_delivery)

    assert anyio.run(filter_.outlet, body, user, metadata) is body


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("PRIVATE timeout URL"),
        httpx.HTTPStatusError(
            "PRIVATE response body",
            request=httpx.Request("POST", "https://private.invalid/events"),
            response=httpx.Response(500),
        ),
        json.JSONDecodeError("PRIVATE invalid JSON", "PRIVATE response", 0),
        RuntimeError("PRIVATE arbitrary failure"),
    ],
)
def test_outlet_delivery_failures_are_content_free_bounded_and_fail_open(
    failure: Exception,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Transport, HTTP, and response failures expose only fixed safe diagnostics."""
    filter_ = Filter()
    filter_.valves = Filter.Valves(hmac_secret="PRIVATE_HMAC_SECRET")
    body, user, metadata = _outlet_fixture(
        user_content="PRIVATE_USER_CONTENT",
        assistant_content="PRIVATE_ASSISTANT_CONTENT",
    )

    async def failed_delivery(_path: str, _payload: dict[str, object]) -> dict[str, object]:
        raise failure

    monkeypatch.setattr(filter_, "_post_signed", failed_delivery)

    assert anyio.run(filter_.outlet, body, user, metadata) is body

    diagnostic = capsys.readouterr().err.strip()
    parsed = json.loads(diagnostic)
    assert parsed == {
        "assistant_content_bytes": len(b"PRIVATE_ASSISTANT_CONTENT"),
        "event_id": filter_._turn_event_id(
            "user-current-owner", "chat-current", "assistant-current"
        ),
        "event_type": "turn.completed.v1",
        "failure": "assistant_core_turn_delivery_failed",
        "user_content_bytes": len(b"PRIVATE_USER_CONTENT"),
    }
    assert len(diagnostic) < 300
    for private in (
        "PRIVATE_USER_CONTENT",
        "PRIVATE_ASSISTANT_CONTENT",
        "PRIVATE timeout URL",
        "PRIVATE response body",
        "https://private.invalid/events",
        "PRIVATE invalid JSON",
        "PRIVATE response",
        "PRIVATE arbitrary failure",
        "PRIVATE_HMAC_SECRET",
    ):
        assert private not in diagnostic


def test_outlet_diagnostic_failure_is_also_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken diagnostic sink cannot affect the native outlet body."""
    filter_ = Filter()
    body, user, metadata = _outlet_fixture()
    diagnostic_attempted = False

    async def failed_delivery(_path: str, _payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("PRIVATE delivery failure")

    def failed_output(*_args: object, **_kwargs: object) -> None:
        nonlocal diagnostic_attempted
        diagnostic_attempted = True
        raise OSError("PRIVATE diagnostic failure")

    monkeypatch.setattr(filter_, "_post_signed", failed_delivery)
    monkeypatch.setattr(context_filter, "print", failed_output, raising=False)

    assert anyio.run(filter_.outlet, body, user, metadata) is body
    assert diagnostic_attempted


def test_outlet_cancellation_and_system_exit_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-open boundary catches ordinary failures, never process exit signals."""
    filter_ = Filter()
    body, user, metadata = _outlet_fixture()

    async def cancelled(_path: str, _payload: dict[str, object]) -> dict[str, object]:
        raise asyncio.CancelledError

    monkeypatch.setattr(filter_, "_post_signed", cancelled)
    with pytest.raises(asyncio.CancelledError):
        anyio.run(filter_.outlet, body, user, metadata)

    async def system_exit(_path: str, _payload: dict[str, object]) -> dict[str, object]:
        raise SystemExit(9)

    monkeypatch.setattr(filter_, "_post_signed", system_exit)
    with pytest.raises(SystemExit, match="9"):
        anyio.run(filter_.outlet, body, user, metadata)


def test_outlet_event_transport_signs_the_exact_canonical_utf8_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The size-checked UTF-8 bytes are exactly the bytes covered by the event HMAC."""
    secret = "event-signing-secret"
    filter_ = Filter()
    filter_.valves = Filter.Valves(
        assistant_core_url="http://companion.test",
        hmac_secret=secret,
    )
    monkeypatch.setattr(context_filter, "datetime", _FixedDatetime)
    monkeypatch.setattr(context_filter.time, "time", lambda: 1_800_000_000)
    captured: dict[str, object] = {}

    async def companion(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"event_id": "accepted", "duplicate": False})

    transport = httpx.MockTransport(companion)
    real_client = httpx.AsyncClient

    def mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return real_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(context_filter.httpx, "AsyncClient", mock_client)
    body, user, metadata = _outlet_fixture(
        user_content="Unicode user 漢",
        assistant_content="Unicode assistant 🙂",
    )

    assert anyio.run(filter_.outlet, body, user, metadata) is body

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    envelope = json.loads(request.content)
    assert request.method == "POST"
    assert request.url.path == "/v1/events"
    assert request.content == filter_._event_bytes(envelope)
    assert b"Unicode user \xe6\xbc\xa2" in request.content
    digest = hashlib.sha256(request.content).hexdigest()
    canonical = f"POST\n/v1/events\n1800000000\n{digest}".encode()
    expected_signature = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    assert request.headers["x-assistant-timestamp"] == "1800000000"
    assert request.headers["x-assistant-signature"] == expected_signature


def test_turn_event_identity_is_deterministic_and_content_independent() -> None:
    """One native assistant response has one collision-safe replay identity."""
    filter_ = Filter()
    identity = json.dumps(
        {
            "native_chat_id": "chat-current",
            "native_message_id": "assistant-current",
            "native_user_id": "user-current-owner",
            "source": "openwebui_outlet_filter",
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    expected = f"turn:v1:{hashlib.sha256(identity).hexdigest()}"

    assert (
        filter_._turn_event_id("user-current-owner", "chat-current", "assistant-current")
        == expected
    )
    assert filter_._turn_event_id(
        "user-current-owner", "chat-current", "assistant-different"
    ) != filter_._turn_event_id("user-current-owner", "chat-current", "assistant-current")


class _RaisingGetMapping(dict[object, object]):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def get(self, _key: object, _default: object = None) -> object:
        raise self.failure


def test_outlet_malformed_mapping_access_is_fail_open_without_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary exception from an adversarial mapping cannot break native chat."""
    filter_ = Filter()
    body = _RaisingGetMapping(RuntimeError("PRIVATE malformed mapping"))

    async def unexpected_delivery(_path: str, _payload: dict[str, object]) -> dict[str, object]:
        pytest.fail("malformed mapping attempted delivery")

    monkeypatch.setattr(filter_, "_post_signed", unexpected_delivery)

    assert anyio.run(filter_.outlet, body, {"id": "user"}, {"user_id": "user"}) is body


def test_outlet_mapping_system_exit_still_propagates() -> None:
    """The broad ordinary-exception shield does not swallow process exit."""
    filter_ = Filter()

    with pytest.raises(SystemExit, match="11"):
        anyio.run(
            filter_.outlet,
            _RaisingGetMapping(SystemExit(11)),
            {"id": "user"},
            {"user_id": "user"},
        )


def test_outlet_ignores_files_when_auto_index_files_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """By default auto_index_files is False so no KB/Vault or chat files are sent for document indexing."""
    filter_ = Filter()
    assert filter_.valves.auto_index_files is False

    delivered_payload: dict[str, object] = {}

    async def mock_post_signed(_path: str, payload: dict[str, object]) -> dict[str, object]:
        delivered_payload.update(payload)
        return {"status": "accepted"}

    monkeypatch.setattr(filter_, "_post_signed", mock_post_signed)

    body, user, metadata = _outlet_fixture(
        user_content="Syncing vault with trigger_sync",
        assistant_content="Vault synced successfully.",
    )
    body["messages"][0]["files"] = [
        {"id": "vault-file-1", "type": "file", "name": "Coin Change.md"},
    ]

    assert anyio.run(filter_.outlet, body, user, metadata) is body
    payload = delivered_payload.get("payload")
    assert isinstance(payload, dict)
    assert "attached_file_ids" not in payload


def test_outlet_captures_attached_file_ids_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When auto_index_files is True, explicit chat attachments are extracted while collection docs are ignored."""
    filter_ = Filter()
    filter_.valves.auto_index_files = True
    delivered_payload: dict[str, object] = {}

    async def mock_post_signed(_path: str, payload: dict[str, object]) -> dict[str, object]:
        delivered_payload.update(payload)
        return {"status": "accepted"}

    monkeypatch.setattr(filter_, "_post_signed", mock_post_signed)

    body, user, metadata = _outlet_fixture(
        user_content="Here is the architecture doc.",
        assistant_content="I see the details.",
    )
    # Body-level files represent auto-retrieved Knowledge Base / RAG docs and must be ignored
    body["files"] = [
        {"id": "kb-rag-auto-retrieved-1", "type": "file"},
        {"id": "kb-obsidian-1", "type": "collection", "collection_name": "obsidian-vault"},
        {"id": "kb-obsidian-2", "type": "knowledge"},
    ]
    # Explicit user message attachments
    body["messages"][0]["files"] = [
        {"id": "file-user-attached-1", "type": "file"},
        {"id": "file-user-attached-2"},
        {"id": "kb-obsidian-3", "collection_id": "vault-1"},
        {"id": "kb-obsidian-4", "meta": {"collection_name": "vault-2"}},
    ]

    assert anyio.run(filter_.outlet, body, user, metadata) is body
    payload = delivered_payload.get("payload")
    assert isinstance(payload, dict)
    assert payload.get("attached_file_ids") == ["file-user-attached-1", "file-user-attached-2"]
