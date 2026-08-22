"""Unit tests for memory consolidation logs and admin endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Any, Self

from starlette.testclient import TestClient

from assistant_core.auth.admin import create_admin_session_token
from assistant_core.config import Settings
from assistant_core.main import create_app
from assistant_core.memory.models import ConsolidationRun


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
        self.committed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, _stmt: Any) -> _FakeResult:
        if self.results:
            return self.results.pop(0)
        return _FakeResult()

    def add(self, item: Any) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.committed = True


def _make_test_client(
    session: _FakeSession, secret: str = "admin-secret-at-least-32-chars-long"
) -> TestClient:
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)
    app.state.session_factory = lambda: session
    client = TestClient(app)
    token = create_admin_session_token(secret)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_list_consolidation_runs_endpoint() -> None:
    """GET /v1/admin/consolidation-runs returns paginated history with details."""
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_run = ConsolidationRun(
        id=run_id,
        native_user_id="user-1",
        trigger="manual_admin",
        status="success",
        memories_scanned=5,
        superseded_count=1,
        details=[
            {
                "superseded_id": str(uuid.uuid4()),
                "superseded_statement": "User uses Python 3.11",
                "superseded_by_id": str(uuid.uuid4()),
                "superseding_statement": "User upgraded to Python 3.12",
                "reason": "Explicit version upgrade",
            }
        ],
        duration_ms=450.2,
        created_at=now,
    )

    fake_session = _FakeSession(
        [
            _FakeResult(scalar=1),  # count_query
            _FakeResult(scalars_list=[mock_run]),  # runs query
        ]
    )

    client = _make_test_client(fake_session)

    resp = client.get("/v1/admin/consolidation-runs")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["id"] == str(run_id)
    assert item["native_user_id"] == "user-1"
    assert item["trigger"] == "manual_admin"
    assert item["status"] == "success"
    assert item["memories_scanned"] == 5
    assert item["superseded_count"] == 1
    assert len(item["details"]) == 1
    assert item["details"][0]["reason"] == "Explicit version upgrade"


def test_get_consolidation_run_detail_endpoint() -> None:
    """GET /v1/admin/consolidation-runs/{run_id} returns single run diff details."""
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_run = ConsolidationRun(
        id=run_id,
        native_user_id="user-2",
        trigger="worker_daily",
        status="no_changes",
        memories_scanned=8,
        superseded_count=0,
        details=[],
        duration_ms=120.5,
        created_at=now,
    )

    fake_session = _FakeSession(
        [
            _FakeResult(scalar=mock_run),  # single run query
        ]
    )

    client = _make_test_client(fake_session)

    resp = client.get(f"/v1/admin/consolidation-runs/{run_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(run_id)
    assert data["native_user_id"] == "user-2"
    assert data["trigger"] == "worker_daily"
    assert data["status"] == "no_changes"
    assert data["memories_scanned"] == 8
    assert data["superseded_count"] == 0
    assert data["details"] == []


def test_get_consolidation_run_not_found() -> None:
    """GET /v1/admin/consolidation-runs/{run_id} returns 404 when not found."""
    fake_session = _FakeSession(
        [
            _FakeResult(scalar=None),
        ]
    )

    client = _make_test_client(fake_session)

    resp = client.get(f"/v1/admin/consolidation-runs/{uuid.uuid4()}")

    assert resp.status_code == 404
