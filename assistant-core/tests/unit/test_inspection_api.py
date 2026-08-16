"""Tests for signed, read-only inspection endpoints."""

import json
import time
import uuid
from typing import Any, Self

import anyio
import httpx
from fastapi import FastAPI

from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.main import create_app


class _FakeResult:
    def __init__(self, *, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        return self._scalar

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeInspectionSession:
    def __init__(self) -> None:
        self.user_id = uuid.uuid4()
        self.statements: list[Any] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, statement: Any) -> _FakeResult:
        self.statements.append(statement)
        sql = str(statement).lower()
        if "user_identity" in sql:
            return _FakeResult(scalar=self.user_id)
        if "file_reference" in sql and "count" in sql:
            return _FakeResult(scalar=5)
        if "file_segment" in sql and "count" in sql:
            return _FakeResult(scalar=3)
        if "job" in sql and "count" in sql:
            return _FakeResult(scalar=0)
        return _FakeResult(rows=[])


def _sign(secret: str, path: str, payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    sig = sign_request(
        secret=secret,
        method="POST",
        path=path,
        timestamp=timestamp,
        body=body_bytes,
    )
    headers = {
        "content-type": "application/json",
        "x-assistant-timestamp": timestamp,
        "x-assistant-signature": sig,
    }
    return body_bytes, headers


def test_inspection_files_stats_requires_signature() -> None:
    settings = Settings(environment="test", hmac_secret="test-secret-at-least-32-bytes-long!")
    app: FastAPI = create_app(settings)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://test",
        ) as client:
            resp = await client.post("/v1/inspection/files/stats", json={"native_user_id": "user-1"})
            assert resp.status_code == 401

    anyio.run(exercise)


def test_inspection_files_stats_success() -> None:
    settings = Settings(environment="test", hmac_secret="test-secret-at-least-32-bytes-long!")
    app: FastAPI = create_app(settings)
    app.state.session_factory = lambda: _FakeInspectionSession()

    path = "/v1/inspection/files/stats"
    payload = {"native_user_id": "user-1"}
    body_bytes, headers = _sign(settings.hmac_secret, path, payload)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://test",
        ) as client:
            resp = await client.post(path, content=body_bytes, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert "total_files" in data
            assert "total_segments" in data
            assert "embedded_segments" in data
            assert "queued_jobs" in data
            assert "dead_jobs" in data

    anyio.run(exercise)


def test_inspection_files_recent_success() -> None:
    settings = Settings(environment="test", hmac_secret="test-secret-at-least-32-bytes-long!")
    app: FastAPI = create_app(settings)
    app.state.session_factory = lambda: _FakeInspectionSession()

    path = "/v1/inspection/files/recent"
    payload = {"native_user_id": "user-1", "limit": 5}
    body_bytes, headers = _sign(settings.hmac_secret, path, payload)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://test",
        ) as client:
            resp = await client.post(path, content=body_bytes, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert "files" in data
            assert isinstance(data["files"], list)

    anyio.run(exercise)


def test_inspection_jobs_dead_success() -> None:
    settings = Settings(environment="test", hmac_secret="test-secret-at-least-32-bytes-long!")
    app: FastAPI = create_app(settings)
    app.state.session_factory = lambda: _FakeInspectionSession()

    path = "/v1/inspection/jobs/dead"
    payload = {"native_user_id": "user-1"}
    body_bytes, headers = _sign(settings.hmac_secret, path, payload)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://test",
        ) as client:
            resp = await client.post(path, content=body_bytes, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert "dead_jobs" in data
            assert isinstance(data["dead_jobs"], list)

    anyio.run(exercise)
