"""Unit tests for the signed event-ingestion route."""

import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
from fastapi import FastAPI

from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.events.schemas import EventEnvelope
from assistant_core.main import create_app

HMAC_SECRET = "a" * 32


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
