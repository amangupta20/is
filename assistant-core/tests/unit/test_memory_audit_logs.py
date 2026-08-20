"""Unit tests for memory change audit logs and granular revert capabilities."""

import uuid
from datetime import UTC, datetime
from typing import Any, Self

import anyio
from starlette.testclient import TestClient

from assistant_core.auth.admin import create_admin_session_token
from assistant_core.config import Settings
from assistant_core.main import create_app
from assistant_core.memory.models import ConsolidationRun, MemoryChangeLog, MemoryRecord
from assistant_core.memory.repository import (
    list_memory_change_logs,
    log_memory_change,
    revert_consolidation_item,
    revert_memory_change_log,
)


class _FakeResult:
    def __init__(
        self,
        *,
        scalar: Any = None,
        rows: list[Any] | None = None,
        scalars_list: list[Any] | None = None,
    ) -> None:
        self._scalar = scalar
        self._rows = rows or []
        self._scalars = scalars_list or []

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def first(self) -> Any:
        return self._scalar or (self._rows[0] if self._rows else None)

    def all(self) -> list[Any]:
        return self._scalars if self._scalars else self._rows

    def scalars(self) -> "_FakeResult":
        return self

    def __iter__(self) -> Any:
        return iter(self._scalars)


class _FakeSession:
    def __init__(self, results: list[_FakeResult] | None = None) -> None:
        self.results = results or []
        self.added: list[Any] = []
        self.flushed = False
        self.committed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def add(self, entity: Any) -> None:
        self.added.append(entity)

    async def flush(self) -> None:
        self.flushed = True

    async def commit(self) -> None:
        self.committed = True

    async def execute(self, _stmt: Any) -> _FakeResult:
        if self.results:
            return self.results.pop(0)
        return _FakeResult(scalar=None)


def test_log_memory_change() -> None:
    session = _FakeSession([_FakeResult(scalar=uuid.uuid4())])

    async def _test() -> None:
        entry = await log_memory_change(
            session,  # type: ignore[arg-type]
            native_user_id="user_123",
            change_source="chat_tool",
            action="create",
            new_state={"statement": "Prefers FastAPI", "category": "preference"},
            reason="Saved via tool",
        )
        assert entry.native_user_id == "user_123"
        assert entry.change_source == "chat_tool"
        assert entry.action == "create"
        assert entry.is_reverted is False
        assert len(session.added) == 1

    anyio.run(_test)


def test_list_memory_change_logs() -> None:
    log_entry = MemoryChangeLog(
        id=uuid.uuid4(),
        native_user_id="user_1",
        change_source="chat_tool",
        action="create",
        created_at=datetime.now(UTC),
    )
    session = _FakeSession([_FakeResult(scalar=1), _FakeResult(scalars_list=[log_entry])])

    async def _test() -> None:
        items, total = await list_memory_change_logs(
            session,  # type: ignore[arg-type]
            native_user_id="user_1",
            change_source="chat_tool",
            page=1,
            page_size=10,
        )
        assert total == 1
        assert len(items) == 1
        assert items[0].change_source == "chat_tool"

    anyio.run(_test)


def test_revert_consolidation_item_supersession() -> None:
    run_id = uuid.uuid4()
    superseded_id = uuid.uuid4()
    superseding_id = uuid.uuid4()

    run = ConsolidationRun(
        id=run_id,
        native_user_id="user_1",
        trigger="worker_daily",
        status="success",
        details=[
            {
                "type": "supersession",
                "superseded_id": str(superseded_id),
                "superseded_statement": "Old fact",
                "superseded_by_id": str(superseding_id),
                "superseding_statement": "New fact",
                "reason": "Direct conflict",
            }
        ],
    )
    rec = MemoryRecord(
        id=superseded_id,
        user_id=uuid.uuid4(),
        key="test.key",
        category="fact",
        statement="Old fact",
        state="superseded",
        superseded_by_id=superseding_id,
    )

    session = _FakeSession([_FakeResult(scalar=run), _FakeResult(scalar=rec)])

    async def _test() -> None:
        res = await revert_consolidation_item(session, run_id=run_id, item_index=0)  # type: ignore[arg-type]
        assert res["success"] is True
        assert run.details[0]["reverted"] is True
        assert rec.state == "active"
        assert rec.superseded_by_id is None

    anyio.run(_test)


def test_revert_consolidation_item_validity_update() -> None:
    run_id = uuid.uuid4()
    mem_id = uuid.uuid4()

    run = ConsolidationRun(
        id=run_id,
        native_user_id="user_1",
        trigger="worker_daily",
        status="success",
        details=[
            {
                "type": "validity_update",
                "action": "expire_now",
                "memory_id": str(mem_id),
                "statement": "Interview event",
                "old_validity": "permanent",
                "new_validity": "expired",
                "reason": "Date passed",
            }
        ],
    )
    rec = MemoryRecord(
        id=mem_id,
        user_id=uuid.uuid4(),
        key="career.interview",
        category="career",
        statement="Interview event",
        state="expired",
    )

    session = _FakeSession([_FakeResult(scalar=run), _FakeResult(scalar=rec)])

    async def _test() -> None:
        res = await revert_consolidation_item(session, run_id=run_id, item_index=0)  # type: ignore[arg-type]
        assert res["success"] is True
        assert run.details[0]["reverted"] is True
        assert rec.state == "active"
        assert rec.expires_at is None

    anyio.run(_test)


def test_revert_consolidation_item_reclassification() -> None:
    run_id = uuid.uuid4()
    mem_id = uuid.uuid4()

    run = ConsolidationRun(
        id=run_id,
        native_user_id="user_1",
        trigger="worker_daily",
        status="success",
        details=[
            {
                "type": "reclassification",
                "memory_id": str(mem_id),
                "statement": "Docker compose server",
                "old_category": "fact",
                "new_category": "infrastructure",
                "reason": "Better domain fit",
            }
        ],
    )
    rec = MemoryRecord(
        id=mem_id,
        user_id=uuid.uuid4(),
        key="infra.docker",
        category="infrastructure",
        statement="Docker compose server",
        state="active",
    )

    session = _FakeSession([_FakeResult(scalar=run), _FakeResult(scalar=rec)])

    async def _test() -> None:
        res = await revert_consolidation_item(session, run_id=run_id, item_index=0)  # type: ignore[arg-type]
        assert res["success"] is True
        assert run.details[0]["reverted"] is True
        assert rec.category == "fact"

    anyio.run(_test)


def test_revert_memory_change_log_update() -> None:
    log_id = uuid.uuid4()
    mem_id = uuid.uuid4()

    log_entry = MemoryChangeLog(
        id=log_id,
        native_user_id="user_1",
        memory_id=mem_id,
        change_source="chat_tool",
        action="update",
        previous_state={
            "statement": "Previous statement",
            "category": "preference",
            "state": "active",
            "temporal_tag": None,
            "expires_at": None,
        },
        new_state={
            "statement": "Modified statement",
            "category": "preference",
            "state": "active",
            "temporal_tag": None,
            "expires_at": None,
        },
        is_reverted=False,
    )
    rec = MemoryRecord(
        id=mem_id,
        user_id=uuid.uuid4(),
        key="pref.style",
        category="preference",
        statement="Modified statement",
        state="active",
    )

    session = _FakeSession([_FakeResult(scalar=log_entry), _FakeResult(scalar=rec)])

    async def _test() -> None:
        res = await revert_memory_change_log(session, log_id=log_id)  # type: ignore[arg-type]
        assert res["success"] is True
        assert log_entry.is_reverted is True
        assert rec.statement == "Previous statement"

    anyio.run(_test)


def _auth_cookie(secret: str = "admin-secret-at-least-32-chars-long") -> dict[str, str]:
    token = create_admin_session_token(secret)
    return {"assistant_admin_session": token}


def test_admin_revert_consolidation_endpoint() -> None:
    secret = "admin-secret-at-least-32-chars-long"
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)

    run_id = uuid.uuid4()
    mem_id = uuid.uuid4()
    run = ConsolidationRun(
        id=run_id,
        native_user_id="user_1",
        trigger="worker_daily",
        status="success",
        details=[
            {
                "type": "reclassification",
                "memory_id": str(mem_id),
                "statement": "Docker setup",
                "old_category": "fact",
                "new_category": "homelab",
                "reason": "Reclassified",
            }
        ],
    )
    rec = MemoryRecord(
        id=mem_id,
        user_id=uuid.uuid4(),
        key="infra.docker",
        category="homelab",
        statement="Docker setup",
        state="active",
    )

    session = _FakeSession([_FakeResult(scalar=run), _FakeResult(scalar=rec)])
    app.state.session_factory = lambda: session

    client = TestClient(app)
    resp = client.post(
        f"/v1/admin/consolidation-runs/{run_id}/revert-item/0",
        cookies=_auth_cookie(secret),
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert session.committed is True


def test_admin_memory_changes_list_and_revert_endpoints() -> None:
    secret = "admin-secret-at-least-32-chars-long"
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)

    log_id = uuid.uuid4()
    mem_id = uuid.uuid4()
    log_entry = MemoryChangeLog(
        id=log_id,
        native_user_id="user_1",
        memory_id=mem_id,
        change_source="chat_tool",
        action="update",
        previous_state={"statement": "Old", "category": "fact", "state": "active"},
        new_state={"statement": "New", "category": "fact", "state": "active"},
        reason="Updated",
        is_reverted=False,
        created_at=datetime.now(UTC),
    )

    # List changes
    session1 = _FakeSession([_FakeResult(scalar=1), _FakeResult(scalars_list=[log_entry])])
    app.state.session_factory = lambda: session1
    client = TestClient(app)

    resp = client.get(
        "/v1/admin/memory-changes?native_user_id=user_1",
        cookies=_auth_cookie(secret),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["action"] == "update"

    # Revert change
    rec = MemoryRecord(
        id=mem_id,
        user_id=uuid.uuid4(),
        key="key1",
        category="fact",
        statement="New",
        state="active",
    )
    session2 = _FakeSession([_FakeResult(scalar=log_entry), _FakeResult(scalar=rec)])
    app.state.session_factory = lambda: session2

    resp_revert = client.post(
        f"/v1/admin/memory-changes/{log_id}/revert",
        cookies=_auth_cookie(secret),
    )
    assert resp_revert.status_code == 200
    assert resp_revert.json()["success"] is True
    assert session2.committed is True
