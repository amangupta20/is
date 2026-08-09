"""Contract tests for the redacted signed status endpoint."""

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi.testclient import TestClient

from assistant_core.main import create_app

TEST_SECRET = "unit-test-status-secret"


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeSession:
    def __init__(self, counts: list[int]) -> None:
        self._counts = iter(counts)
        self.statements: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any) -> _ScalarResult:
        self.statements.append(statement)
        return _ScalarResult(next(self._counts))

    async def commit(self) -> None:
        self.commits += 1


class _FakeSessionFactory:
    def __init__(self, counts: list[int]) -> None:
        self.counts = counts
        self.entries = 0
        self.sessions: list[_FakeSession] = []

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[_FakeSession]:
        self.entries += 1
        session = _FakeSession(self.counts)
        self.sessions.append(session)
        yield session


def _signed_headers(body: bytes, path: str = "/v1/status") -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"POST\n{path}\n{timestamp}\n{digest}".encode()
    signature = hmac.new(TEST_SECRET.encode(), canonical, hashlib.sha256).hexdigest()
    return {
        "content-type": "application/json",
        "x-assistant-timestamp": timestamp,
        "x-assistant-signature": signature,
    }


def _app_with_factory(factory: _FakeSessionFactory):
    app = create_app()
    app.state.settings.hmac_secret = TEST_SECRET
    app.state.session_factory = factory
    return app


def test_signed_status_returns_only_aggregate_queue_counts() -> None:
    factory = _FakeSessionFactory([7, 2])
    app = _app_with_factory(factory)
    payload = {
        "native_user_id": "native-user",
        "native_chat_id": "native-chat",
        "native_message_id": "native-message",
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    with TestClient(app) as client:
        response = client.post("/v1/status", content=body, headers=_signed_headers(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "queued_jobs": 7, "dead_jobs": 2}
    assert factory.entries == 1
    assert len(factory.sessions[0].statements) == 2
    assert factory.sessions[0].commits == 0


def test_unsigned_status_is_rejected_before_opening_session() -> None:
    factory = _FakeSessionFactory([0, 0])
    app = _app_with_factory(factory)

    with TestClient(app) as client:
        response = client.post("/v1/status", json={"native_user_id": "native-user"})

    assert response.status_code == 401
    assert factory.entries == 0


def test_invalid_signature_is_rejected_before_opening_session() -> None:
    factory = _FakeSessionFactory([0, 0])
    app = _app_with_factory(factory)
    body = b'{"native_user_id":"native-user"}'
    headers = _signed_headers(body)
    headers["x-assistant-signature"] = "0" * 64

    with TestClient(app) as client:
        response = client.post("/v1/status", content=body, headers=headers)

    assert response.status_code == 401
    assert factory.entries == 0


def test_malformed_status_request_is_rejected_before_opening_session() -> None:
    factory = _FakeSessionFactory([0, 0])
    app = _app_with_factory(factory)
    bodies = [
        b'{"native_user_id":""}',
        json.dumps({"native_user_id": "u" * 201}, separators=(",", ":")).encode(),
        b'{"native_user_id":"u","unknown":"not-allowed"}',
    ]

    with TestClient(app) as client:
        for body in bodies:
            response = client.post("/v1/status", content=body, headers=_signed_headers(body))
            assert response.status_code == 422

    assert factory.entries == 0
