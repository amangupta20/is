"""Unit tests for the admin management endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Any, Self

import pytest
from starlette.testclient import TestClient

from assistant_core.auth.admin import create_admin_session_token
from assistant_core.config import Settings
from assistant_core.jobs.models import Job
from assistant_core.main import create_app


class _FakeResult:
    def __init__(
        self,
        *,
        scalar: Any = None,
        rows: list[Any] | None = None,
        scalars_list: list[Any] | None = None,
        rowcount: int = 1,
    ) -> None:
        self._scalar = scalar
        self._rows = rows or []
        self._scalars = scalars_list or []
        self.rowcount = rowcount

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def first(self) -> Any:
        return self._scalar or (self._rows[0] if self._rows else None)

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> "_FakeResult":
        return self

    def __iter__(self) -> Any:
        return iter(self._scalars)


class _FakeSession:
    def __init__(self, results: list[_FakeResult] | None = None) -> None:
        self.results = results or []
        self.added: list[Any] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def add(self, entity: Any) -> None:
        self.added.append(entity)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def execute(self, _statement: Any) -> _FakeResult:
        if self.results:
            return self.results.pop(0)
        return _FakeResult()


def _get_authed_client(
    app: Any, secret: str = "test-secret-at-least-32-characters-long"
) -> TestClient:
    client = TestClient(app)
    token = create_admin_session_token(secret)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_admin_login_and_logout() -> None:
    secret = "admin-secret-at-least-32-chars-long"
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)
    client = TestClient(app)

    # 1. Failed login with wrong secret
    fail_res = client.post("/v1/admin/auth/login", json={"token": "wrong-token"})
    assert fail_res.status_code == 401

    # 2. Successful login
    succ_res = client.post("/v1/admin/auth/login", json={"token": secret})
    assert succ_res.status_code == 200
    assert succ_res.json()["status"] == "authenticated"
    assert "assistant_admin_session" in succ_res.cookies

    # 3. Auth check with cookie
    check_res = client.get("/v1/admin/auth/check")
    assert check_res.status_code == 200
    assert check_res.json() == {"authenticated": True}

    # 4. Logout
    logout_res = client.post("/v1/admin/auth/logout")
    assert logout_res.status_code == 200


def test_admin_overview_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "admin-secret-at-least-32-chars-long"
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)

    session = _FakeSession(
        [
            _FakeResult(scalar=12),  # active memories
            _FakeResult(scalar=3),  # tombstoned memories
            _FakeResult(scalar=5),  # active files
            _FakeResult(scalar=1),  # tombstoned files
            _FakeResult(scalar=25000),  # total file characters
            _FakeResult(scalar=45),  # total file segments
            _FakeResult(scalar=45),  # embedded segments
            _FakeResult(scalar=50),  # total references
            _FakeResult(scalar=80),  # active turns
            _FakeResult(scalar=120),  # indexed passages
            _FakeResult(rows=[("queued", 2), ("dead", 1)]),  # job counts
        ]
    )
    app.state.session_factory = lambda: session

    client = _get_authed_client(app, secret)
    resp = client.get("/v1/admin/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["memories"]["active"] == 12
    assert data["files"]["active"] == 5
    assert data["files"]["total_characters"] == 25000
    assert data["jobs"]["queued"] == 2
    assert data["jobs"]["dead"] == 1


def test_admin_memories_crud() -> None:
    secret = "admin-secret-at-least-32-chars-long"
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)

    mem_id = uuid.uuid4()
    now = datetime.now(UTC)
    list_session = _FakeSession(
        [
            _FakeResult(scalar=1),  # total count
            _FakeResult(
                rows=[
                    (
                        mem_id,
                        "User prefers concise answers",
                        "preference",
                        0.95,
                        "active",
                        now,
                        None,
                        "user-1",
                    )
                ]
            ),
        ]
    )
    app.state.session_factory = lambda: list_session

    client = _get_authed_client(app, secret)

    # 1. List
    list_res = client.get("/v1/admin/memories")
    assert list_res.status_code == 200
    assert list_res.json()["total"] == 1
    assert list_res.json()["items"][0]["statement"] == "User prefers concise answers"

    # 2. Create
    user_id = uuid.uuid4()
    create_session = _FakeSession(
        [
            _FakeResult(scalar=user_id),  # existing user
        ]
    )
    app.state.session_factory = lambda: create_session
    create_res = client.post(
        "/v1/admin/memories",
        json={
            "native_user_id": "user-1",
            "statement": "New manual memory",
            "category": "fact",
            "confidence": 1.0,
        },
    )
    assert create_res.status_code == 200
    assert create_res.json()["statement"] == "New manual memory"


def test_admin_files_and_jobs_endpoints() -> None:
    secret = "admin-secret-at-least-32-chars-long"
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)

    file_id = uuid.uuid4()
    now = datetime.now(UTC)

    class FakeDoc:
        id = file_id
        native_file_id = "doc-123"
        filename = "architecture.pdf"
        mime_type = "application/pdf"
        total_chunks = 4
        total_characters = 1500
        content = "# Architecture\nSystem breakdown."
        user_id = uuid.uuid4()
        created_at = now
        tombstoned_at = None

    session = _FakeSession(
        [
            # For GET /v1/admin/files
            _FakeResult(scalar=1),  # count
            _FakeResult(
                rows=[
                    (
                        file_id,
                        "doc-123",
                        "architecture.pdf",
                        "application/pdf",
                        4,
                        1500,
                        now,
                        None,
                        "user-1",
                    )
                ]
            ),
            # For GET /v1/admin/files/doc-123
            _FakeResult(scalar=FakeDoc()),
            _FakeResult(
                rows=[
                    (0, "Overview", "hash-0", 250, True),
                    (1, "Database", "hash-1", 400, True),
                ]
            ),
            # For POST /v1/admin/jobs/{id}/retry
            _FakeResult(
                scalar=Job(id=uuid.uuid4(), kind="index_file", identity_key="f:1", status="dead")
            ),
        ]
    )
    app.state.session_factory = lambda: session
    client = _get_authed_client(app, secret)

    # 1. List files
    files_res = client.get("/v1/admin/files")
    assert files_res.status_code == 200
    assert files_res.json()["total"] == 1
    assert files_res.json()["items"][0]["filename"] == "architecture.pdf"

    # 2. File detail
    detail_res = client.get("/v1/admin/files/doc-123")
    assert detail_res.status_code == 200
    assert detail_res.json()["filename"] == "architecture.pdf"
    assert "# Architecture" in detail_res.json()["content"]
    assert len(detail_res.json()["chunks"]) == 2

    # 3. Retry dead job
    job_uuid = uuid.uuid4()
    retry_res = client.post(f"/v1/admin/jobs/{job_uuid}/retry")
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "requeued"


def test_dashboard_ui_served() -> None:
    secret = "admin-secret-at-least-32-chars-long"
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)

    with TestClient(app) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert "Assistant Core" in res.text
        assert "text/html" in res.headers.get("content-type", "")

        js_res = client.get("/static/app.js")
        assert js_res.status_code == 200

        css_res = client.get("/static/styles.css")
        assert css_res.status_code == 200


def test_admin_batch_delete_and_purge() -> None:
    secret = "admin-secret-at-least-32-chars-long"
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)

    session = _FakeSession(
        [
            # 1. Batch delete memories: 3 queries (evidence delete, self-ref update, records delete)
            _FakeResult(rowcount=2),
            _FakeResult(rowcount=0),
            _FakeResult(rowcount=2),
            # 2. Batch delete files: 3 queries (references delete, documents delete, orphan segments delete)
            _FakeResult(rowcount=3),
            _FakeResult(rowcount=1),
            _FakeResult(rowcount=3),
            # 3. Batch delete conversations: 4 queries (evidence delete, conv refs delete, turns delete, orphan segments delete)
            _FakeResult(rowcount=1),
            _FakeResult(rowcount=2),
            _FakeResult(rowcount=2),
            _FakeResult(rowcount=2),
            # 4. System purge all: evidence, self-ref, records, snapshots, file refs, file docs, file segs, conv refs, conv segs, turns, jobs, events
            _FakeResult(rowcount=5),
            _FakeResult(rowcount=0),
            _FakeResult(rowcount=5),
            _FakeResult(rowcount=1),
            _FakeResult(rowcount=10),
            _FakeResult(rowcount=2),
            _FakeResult(rowcount=8),
            _FakeResult(rowcount=12),
            _FakeResult(rowcount=12),
            _FakeResult(rowcount=6),
            _FakeResult(rowcount=4),
            _FakeResult(rowcount=10),
        ]
    )
    app.state.session_factory = lambda: session
    client = _get_authed_client(app, secret)

    # 1. Batch delete memories
    m_res = client.post(
        "/v1/admin/memories/batch-delete",
        json={"ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
    )
    assert m_res.status_code == 200
    assert m_res.json()["status"] == "deleted"
    assert m_res.json()["count"] == 2

    # 2. Batch delete files
    f_res = client.post(
        "/v1/admin/files/batch-delete",
        json={"native_file_ids": ["doc-1", "doc-2"]},
    )
    assert f_res.status_code == 200
    assert f_res.json()["status"] == "deleted"
    assert f_res.json()["count"] == 1

    # 3. Batch delete conversations
    c_res = client.post(
        "/v1/admin/conversations/batch-delete",
        json={"ids": [str(uuid.uuid4())]},
    )
    assert c_res.status_code == 200
    assert c_res.json()["status"] == "deleted"
    assert c_res.json()["count"] == 2

    # 4. System purge with wrong confirmation (rejected)
    bad_purge_res = client.post(
        "/v1/admin/system/purge",
        json={"confirmation": "wrong", "scope": "all"},
    )
    assert bad_purge_res.status_code == 400

    # 5. System purge with valid confirmation
    good_purge_res = client.post(
        "/v1/admin/system/purge",
        json={"confirmation": "PURGE", "scope": "all"},
    )
    assert good_purge_res.status_code == 200
    assert good_purge_res.json()["status"] == "purged"
    assert good_purge_res.json()["scope"] == "all"
    assert "memories" in good_purge_res.json()["deleted"]
    assert "file_documents" in good_purge_res.json()["deleted"]
    assert "completed_turns" in good_purge_res.json()["deleted"]
