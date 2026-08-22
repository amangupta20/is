"""Tests for signed direct memory CRUD endpoints in personal_context."""

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Self

import anyio
import httpx
from fastapi import FastAPI

from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.identity.models import UserIdentity
from assistant_core.main import create_app
from assistant_core.memory.models import MemoryRecord


class _FakeResult:
    def __init__(self, *, rows: list[object] | None = None, scalar: object | None = None) -> None:
        self.rows = rows or []
        self.scalar_val = scalar

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[object]:
        return self.rows

    def scalar_one_or_none(self) -> object | None:
        return self.scalar_val


class _DirectMemorySession:
    def __init__(self, user_id: uuid.UUID, memory_records: list[MemoryRecord]) -> None:
        self.user_id = user_id
        self.records = memory_records
        self.statements: list[object] = []
        self.added: list[object] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, statement: object) -> _FakeResult:
        self.statements.append(statement)
        sql = str(statement).lower()
        if "user_identity" in sql and "select" in sql:
            if (
                "user_identity.id \nfrom" in sql
                or "user_identity.id\nfrom" in sql
                or "user_identity.id from" in sql
            ):
                return _FakeResult(scalar=self.user_id, rows=[self.user_id])
            return _FakeResult(scalar=UserIdentity(id=self.user_id, native_user_id="user-1"))
        if "memory_record" in sql and "select" in sql:
            return _FakeResult(rows=self.records, scalar=self.records[0] if self.records else None)
        return _FakeResult(scalar=None, rows=[])

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


def _build_app(session_factory: Any) -> FastAPI:
    settings = Settings(
        admin_token="admin-token",
        hmac_secret="a" * 32,
        open_webui_url="http://openwebui.example",
        task_model_base_url="http://task-model.example",
        task_model_model="task-model",
    )
    app = create_app(settings)
    app.state.session_factory = session_factory
    return app


async def _post(app: FastAPI, path: str, body: dict[str, Any]) -> httpx.Response:
    request_body = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    signature = sign_request("a" * 32, "POST", path, timestamp, request_body)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            path,
            content=request_body,
            headers={
                "content-type": "application/json",
                "x-assistant-timestamp": timestamp,
                "x-assistant-signature": signature,
            },
        )


def test_list_memories_endpoint() -> None:
    user_id = uuid.uuid4()
    mem_1 = MemoryRecord(
        id=uuid.uuid4(),
        user_id=user_id,
        key="career.datazip",
        category="career",
        statement="User applied for Datazip role",
        state="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = _DirectMemorySession(user_id, [mem_1])
    app = _build_app(lambda: session)

    body = {"native_user_id": "user-1", "query": "datazip"}

    async def run_test() -> None:
        response = await _post(app, "/v1/personal-context/memory/list", body)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["memories"][0]["key"] == "career.datazip"
        assert data["memories"][0]["category"] == "career"

    anyio.run(run_test)


def test_save_memory_endpoint() -> None:
    user_id = uuid.uuid4()
    session = _DirectMemorySession(user_id, [])
    app = _build_app(lambda: session)

    body = {
        "native_user_id": "user-1",
        "key": "infra.dokploy",
        "statement": "Runs Dokploy on VPS",
        "category": "infrastructure",
        "temporal_tag": "in_progress",
    }

    async def run_test() -> None:
        response = await _post(app, "/v1/personal-context/memory/save", body)
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        assert data["memory"]["key"] == "infra.dokploy"
        assert data["memory"]["category"] == "infrastructure"

    anyio.run(run_test)


def test_update_memory_endpoint() -> None:
    user_id = uuid.uuid4()
    mem_id = uuid.uuid4()
    mem = MemoryRecord(
        id=mem_id,
        user_id=user_id,
        key="infra.dokploy",
        category="infrastructure",
        statement="Runs Dokploy on VPS",
        state="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = _DirectMemorySession(user_id, [mem])
    app = _build_app(lambda: session)

    body = {
        "native_user_id": "user-1",
        "memory_id": str(mem_id),
        "statement": "Runs Dokploy with Traefik reverse proxy",
        "temporal_tag": "active_task",
    }

    async def run_test() -> None:
        response = await _post(app, "/v1/personal-context/memory/update", body)
        assert response.status_code == 200
        data = response.json()
        assert data["memory"]["statement"] == "Runs Dokploy with Traefik reverse proxy"

    anyio.run(run_test)


def test_forget_memory_endpoint() -> None:
    user_id = uuid.uuid4()
    mem_id = uuid.uuid4()
    mem = MemoryRecord(
        id=mem_id,
        user_id=user_id,
        key="infra.dokploy",
        category="infrastructure",
        statement="Runs Dokploy on VPS",
        state="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = _DirectMemorySession(user_id, [mem])
    app = _build_app(lambda: session)

    body = {
        "native_user_id": "user-1",
        "memory_id": str(mem_id),
        "reason": "Deprecated old infrastructure",
    }

    async def run_test() -> None:
        response = await _post(app, "/v1/personal-context/memory/forget", body)
        assert response.status_code == 200
        data = response.json()
        assert data["archived_count"] == 1
        assert data["archived_ids"] == [str(mem_id)]

    anyio.run(run_test)
