"""Behaviour tests for the standalone Assistant Context Filter."""

import copy
import hashlib
import hmac
import importlib
import json
import sys
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


def test_filter_valves_default_to_and_accept_the_open_webui_maximum() -> None:
    """The Valve budget matches the largest value accepted by Open WebUI's UI."""
    assert Filter.Valves().max_context_tokens == 9_999
    assert Filter.Valves(max_context_tokens=9_999).max_context_tokens == 9_999


def test_filter_valves_reject_a_budget_above_the_open_webui_maximum() -> None:
    """Open WebUI must not persist a budget its numeric UI rejects."""
    with pytest.raises(ValidationError):
        Filter.Valves(max_context_tokens=10_000)


def test_empty_context_leaves_the_native_body_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """An intentionally empty response does not alter the native prompt."""
    filter_ = Filter()
    body = {"messages": [{"role": "user", "content": "hello"}]}
    original = copy.deepcopy(body)

    async def empty_context(_: dict) -> dict:
        return {"context_text": "", "token_estimate": 0, "sources": [], "degraded": False}

    monkeypatch.setattr(filter_, "_post_context", empty_context)

    assert anyio.run(lambda: filter_.inlet(body, __user__={"id": "u"})) == original


def test_timeout_leaves_the_native_body_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded companion timeout is invisible to the native chat request."""
    filter_ = Filter()
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
    )
    expected_payload = {
        "native_user_id": "u-1",
        "native_chat_id": "chat-1",
        "native_message_id": "message-1",
        "request_text": "x" * 16_000,
        "max_tokens": 9_999,
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


def test_nonempty_context_is_inserted_before_the_latest_user_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Future context sits directly before the latest user request, once."""
    filter_ = Filter()
    body = {
        "messages": [
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
        {"role": "assistant", "content": "Earlier answer"},
        {
            "role": "system",
            "content": "<assistant_context>\nPreferred response style: direct.\n</assistant_context>",
        },
        {"role": "user", "content": "Latest request"},
        {"role": "assistant", "content": "Generated continuation"},
    ]
