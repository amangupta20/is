"""Unit tests for the signed event-ingestion route."""

import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from hashlib import sha256
from typing import Any

import anyio
import httpx
import pytest
from fastapi import FastAPI

from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.events.schemas import EventEnvelope
from assistant_core.main import create_app

HMAC_SECRET = "a" * 32
MAX_COMPLETED_TURN_REQUEST_BYTES = 524_288


class FakeSession:
    """Record transaction entry without providing database behavior."""

    def __init__(self) -> None:
        self.transaction_entries = 0

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        self.transaction_entries += 1
        yield


def session_factory(session: FakeSession) -> Callable[[], Any]:
    """Return an async context-manager factory around one fake session."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeSession]:
        yield session

    return factory


def event_body(**changes: object) -> bytes:
    """Encode a valid event payload with selected changes."""
    event: dict[str, object] = {
        "schema_version": 1,
        "event_id": "event-1",
        "event_type": "chat.completed",
        "native_user_id": "user-1",
        "occurred_at": "2026-08-09T12:00:00Z",
        "payload": {"source": "openwebui"},
    }
    event.update(changes)
    return json.dumps(event, separators=(",", ":")).encode()


def canonical_event_body(event: dict[str, object]) -> bytes:
    """Match the adapter's sorted, compact, Unicode-preserving serialization."""
    return json.dumps(
        event,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def completed_turn_body(size: int) -> bytes:
    """Build an exactly sized, canonical completed-turn request with Unicode."""
    user_content = "नमस्ते"
    assistant_content = "界"
    event: dict[str, object] = {
        "schema_version": 1,
        "event_id": "turn:v1:exact-boundary",
        "event_type": "turn.completed.v1",
        "native_user_id": "user-1",
        "native_chat_id": "chat-1",
        "native_message_id": "assistant-1",
        "occurred_at": "2026-08-10T12:00:00Z",
        "payload": {
            "source": "openwebui_outlet_filter",
            "user_message": {
                "id": "user-message-1",
                "role": "user",
                "content": user_content,
                "sha256": sha256(user_content.encode()).hexdigest(),
            },
            "assistant_message": {
                "id": "assistant-1",
                "role": "assistant",
                "content": assistant_content,
                "sha256": sha256(assistant_content.encode()).hexdigest(),
            },
        },
    }
    initial = canonical_event_body(event)
    padding = size - len(initial)
    assert padding >= 0
    assistant_content += "x" * padding
    assistant = event["payload"]["assistant_message"]  # type: ignore[index]
    assistant["content"] = assistant_content  # type: ignore[index]
    assistant["sha256"] = sha256(assistant_content.encode()).hexdigest()  # type: ignore[index]
    body = canonical_event_body(event)
    assert len(body) == size
    assert "界".encode() in body
    return body


def oversized_turn_payload() -> dict[str, object]:
    """Return the exact metadata-only oversized-turn payload."""
    return {
        "source": "openwebui_outlet_filter",
        "user_message": {
            "id": "user-message-1",
            "sha256": "a" * 64,
            "content_bytes": 123,
        },
        "assistant_message": {
            "id": "assistant-message-1",
            "sha256": "b" * 64,
            "content_bytes": 456,
        },
    }


def oversized_turn_event() -> dict[str, object]:
    """Return one complete canonical oversized-turn envelope."""
    return {
        "schema_version": 1,
        "event_id": "turn:v1:oversized-envelope",
        "event_type": "turn.oversized.v1",
        "native_user_id": "user-1",
        "native_chat_id": "chat-1",
        "native_message_id": "assistant-message-1",
        "occurred_at": "2026-08-10T12:00:00Z",
        "payload": oversized_turn_payload(),
    }


async def send_event(app: FastAPI, body: bytes, *, signed: bool = True) -> httpx.Response:
    """Send an event with an optional valid adapter signature."""
    headers = {"Content-Type": "application/json"}
    if signed:
        timestamp = str(int(time.time()))
        headers.update(
            {
                "X-Assistant-Timestamp": timestamp,
                "X-Assistant-Signature": sign_request(
                    HMAC_SECRET, "POST", "/v1/events", timestamp, body
                ),
            }
        )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/v1/events", content=body, headers=headers)


def test_signed_event_runs_ingestion_in_route_owned_transaction(monkeypatch: Any) -> None:
    """The HTTP route enters one transaction and returns the service duplicate result."""
    app = create_app(Settings(hmac_secret=HMAC_SECRET))
    fake_session = FakeSession()
    app.state.session_factory = session_factory(fake_session)
    ingested: list[tuple[FakeSession, EventEnvelope]] = []

    async def fake_ingest(session: FakeSession, event: EventEnvelope) -> bool:
        ingested.append((session, event))
        return False

    monkeypatch.setattr("assistant_core.api.routes.events.ingest_event", fake_ingest)

    response = anyio.run(send_event, app, event_body())

    assert response.status_code == 202
    assert response.json() == {"event_id": "event-1", "duplicate": False}
    assert fake_session.transaction_entries == 1
    assert [(session, event.event_id) for session, event in ingested] == [
        (fake_session, "event-1")
    ]


def test_authentication_and_validation_fail_before_ingestion(monkeypatch: Any) -> None:
    """Unauthorized and invalid bodies never open a database transaction or call the service."""
    app = create_app(Settings(hmac_secret=HMAC_SECRET))
    fake_session = FakeSession()
    app.state.session_factory = session_factory(fake_session)
    calls: list[object] = []

    async def fake_ingest(session: FakeSession, event: EventEnvelope) -> bool:
        calls.append((session, event))
        return False

    monkeypatch.setattr("assistant_core.api.routes.events.ingest_event", fake_ingest)

    unsigned = anyio.run(lambda: send_event(app, event_body(), signed=False))
    invalid = anyio.run(send_event, app, event_body(event_id=""))

    assert unsigned.status_code == 401
    assert invalid.status_code == 422
    assert calls == []
    assert fake_session.transaction_entries == 0


def test_completed_turn_raw_request_limit_accepts_exact_bytes_and_rejects_plus_one(
    monkeypatch: Any,
) -> None:
    """Signed canonical requests enforce the exact 512 KiB raw-byte boundary."""
    app = create_app(Settings(hmac_secret=HMAC_SECRET))
    fake_session = FakeSession()
    app.state.session_factory = session_factory(fake_session)
    ingested_sizes: list[int] = []

    async def fake_ingest(_session: FakeSession, _event: EventEnvelope) -> bool:
        ingested_sizes.append(MAX_COMPLETED_TURN_REQUEST_BYTES)
        return False

    monkeypatch.setattr("assistant_core.api.routes.events.ingest_event", fake_ingest)
    exact_body = completed_turn_body(MAX_COMPLETED_TURN_REQUEST_BYTES)
    oversized_body = completed_turn_body(MAX_COMPLETED_TURN_REQUEST_BYTES + 1)
    above_generic_payload_limit = completed_turn_body(1_000_500)

    exact = anyio.run(send_event, app, exact_body)
    too_large = anyio.run(send_event, app, oversized_body)
    far_too_large = anyio.run(send_event, app, above_generic_payload_limit)
    unsigned_too_large = anyio.run(
        lambda: send_event(app, above_generic_payload_limit, signed=False)
    )

    assert exact.status_code == 202
    assert too_large.status_code == 413
    assert too_large.json() == {"detail": "turn_completed_request_too_large"}
    assert far_too_large.status_code == 413
    assert far_too_large.json() == {"detail": "turn_completed_request_too_large"}
    assert unsigned_too_large.status_code == 401
    assert ingested_sizes == [MAX_COMPLETED_TURN_REQUEST_BYTES]
    assert fake_session.transaction_entries == 1


def test_unrelated_lifecycle_event_preserves_generic_one_megabyte_behavior(
    monkeypatch: Any,
) -> None:
    """The turn-specific request limit does not narrow generic event ingestion."""
    app = create_app(Settings(hmac_secret=HMAC_SECRET))
    fake_session = FakeSession()
    app.state.session_factory = session_factory(fake_session)
    calls: list[str] = []

    async def fake_ingest(_session: FakeSession, event: EventEnvelope) -> bool:
        calls.append(event.event_type)
        return False

    monkeypatch.setattr("assistant_core.api.routes.events.ingest_event", fake_ingest)
    body = event_body(
        event_type="chat.lifecycle.v1",
        payload={"source": "openwebui", "metadata": "x" * 600_000},
    )

    response = anyio.run(send_event, app, body)

    assert response.status_code == 202
    assert calls == ["chat.lifecycle.v1"]
    assert fake_session.transaction_entries == 1


def test_exact_oversized_turn_metadata_envelope_is_accepted(monkeypatch: Any) -> None:
    """The adapter's exact content-free oversized envelope remains compatible."""
    app = create_app(Settings(hmac_secret=HMAC_SECRET))
    fake_session = FakeSession()
    app.state.session_factory = session_factory(fake_session)
    calls: list[dict[str, object]] = []

    async def fake_ingest(_session: FakeSession, event: EventEnvelope) -> bool:
        calls.append(event.payload)
        return False

    monkeypatch.setattr("assistant_core.api.routes.events.ingest_event", fake_ingest)
    body = event_body(
        event_type="turn.oversized.v1",
        native_chat_id="chat-1",
        native_message_id="assistant-message-1",
        payload=oversized_turn_payload(),
    )

    response = anyio.run(send_event, app, body)

    assert response.status_code == 202
    assert calls == [oversized_turn_payload()]
    assert fake_session.transaction_entries == 1


@pytest.mark.parametrize(
    ("native_chat_id", "native_message_id"),
    [
        (None, "assistant-message-1"),
        ("", "assistant-message-1"),
        ("chat-1", None),
        ("chat-1", "different-assistant-message"),
    ],
)
def test_oversized_turn_requires_compatible_native_envelope_ids(
    monkeypatch: Any,
    native_chat_id: str | None,
    native_message_id: str | None,
) -> None:
    """Metadata-only turn IDs must agree with required envelope provenance."""
    app = create_app(Settings(hmac_secret=HMAC_SECRET))
    fake_session = FakeSession()
    app.state.session_factory = session_factory(fake_session)
    calls: list[object] = []

    async def fake_ingest(_session: FakeSession, event: EventEnvelope) -> bool:
        calls.append(event)
        return False

    monkeypatch.setattr("assistant_core.api.routes.events.ingest_event", fake_ingest)
    response = anyio.run(
        send_event,
        app,
        event_body(
            event_type="turn.oversized.v1",
            native_chat_id=native_chat_id,
            native_message_id=native_message_id,
            payload=oversized_turn_payload(),
        ),
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_turn_oversized_payload"}
    assert calls == []
    assert fake_session.transaction_entries == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"unexpected": "private-content-marker"}),
        lambda payload: payload["user_message"].update(  # type: ignore[union-attr]
            {"content": "private-content-marker"}
        ),
        lambda payload: payload["assistant_message"].update(  # type: ignore[union-attr]
            {"role": "assistant"}
        ),
        lambda payload: payload["user_message"].update(  # type: ignore[union-attr]
            {"timestamp": 1}
        ),
        lambda payload: payload["user_message"].update(  # type: ignore[union-attr]
            {"content_bytes": -1}
        ),
        lambda payload: payload["assistant_message"].update(  # type: ignore[union-attr]
            {"content_bytes": True}
        ),
        lambda payload: payload["assistant_message"].update(  # type: ignore[union-attr]
            {"sha256": "B" * 64}
        ),
        lambda payload: payload["user_message"].update(  # type: ignore[union-attr]
            {"id": " "}
        ),
        lambda payload: payload.update({"source": "private-content-marker"}),
    ],
)
def test_invalid_oversized_turn_payload_is_rejected_with_one_safe_error(
    monkeypatch: Any, mutation: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Content, extras, coercion, and noncanonical metadata fail without leakage."""
    app = create_app(Settings(hmac_secret=HMAC_SECRET))
    fake_session = FakeSession()
    app.state.session_factory = session_factory(fake_session)
    calls: list[object] = []
    payload = oversized_turn_payload()
    mutation(payload)

    async def fake_ingest(_session: FakeSession, event: EventEnvelope) -> bool:
        calls.append(event)
        return False

    monkeypatch.setattr("assistant_core.api.routes.events.ingest_event", fake_ingest)
    response = anyio.run(
        send_event,
        app,
        event_body(
            event_type="turn.oversized.v1",
            native_chat_id="chat-1",
            native_message_id="assistant-message-1",
            payload=payload,
        ),
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_turn_oversized_payload"}
    assert "private-content-marker" not in response.text
    assert HMAC_SECRET not in response.text
    assert "private-content-marker" not in caplog.text
    assert HMAC_SECRET not in caplog.text
    assert calls == []
    assert fake_session.transaction_entries == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda envelope: envelope.update(
            {"unexpected": "PRIVATE_TOP_LEVEL_CONTENT"}
        ),
        lambda envelope: envelope.pop("schema_version"),
        lambda envelope: envelope.update({"schema_version": "1"}),
        lambda envelope: envelope.update({"schema_version": True}),
        lambda envelope: envelope.pop("event_id"),
        lambda envelope: envelope.update({"event_id": "x" * 201}),
        lambda envelope: envelope.update({"event_id": 123}),
        lambda envelope: envelope.pop("event_type"),
        lambda envelope: envelope.update({"event_type": "turn.oversized.v2"}),
        lambda envelope: envelope.update({"event_type": 1}),
        lambda envelope: envelope.pop("native_user_id"),
        lambda envelope: envelope.update({"native_user_id": "x" * 201}),
        lambda envelope: envelope.update({"native_user_id": 123}),
        lambda envelope: envelope.pop("native_chat_id"),
        lambda envelope: envelope.update({"native_chat_id": ""}),
        lambda envelope: envelope.update({"native_chat_id": "x" * 201}),
        lambda envelope: envelope.pop("native_message_id"),
        lambda envelope: envelope.update({"native_message_id": "x" * 201}),
        lambda envelope: envelope.pop("occurred_at"),
        lambda envelope: envelope.update(
            {"occurred_at": "2026-08-10T12:00:00"}
        ),
        lambda envelope: envelope.update(
            {"occurred_at": "PRIVATE_TOP_LEVEL_CONTENT"}
        ),
        lambda envelope: envelope.update({"occurred_at": 1_800_000_000}),
    ],
)
def test_invalid_oversized_turn_envelope_always_uses_safe_prevalidation_error(
    monkeypatch: Any,
    mutation: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every oversized-envelope failure is sanitized before FastAPI validation."""
    app = create_app(Settings(hmac_secret=HMAC_SECRET))
    fake_session = FakeSession()
    app.state.session_factory = session_factory(fake_session)
    calls: list[object] = []
    envelope = oversized_turn_event()
    mutation(envelope)

    async def fake_ingest(_session: FakeSession, event: EventEnvelope) -> bool:
        calls.append(event)
        return False

    monkeypatch.setattr("assistant_core.api.routes.events.ingest_event", fake_ingest)
    response = anyio.run(send_event, app, canonical_event_body(envelope))

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_turn_oversized_payload"}
    assert "PRIVATE_TOP_LEVEL_CONTENT" not in response.text
    assert HMAC_SECRET not in response.text
    assert "PRIVATE_TOP_LEVEL_CONTENT" not in caplog.text
    assert HMAC_SECRET not in caplog.text
    assert calls == []
    assert fake_session.transaction_entries == 0
