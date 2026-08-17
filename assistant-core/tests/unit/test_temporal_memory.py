"""Unit tests for Temporal Memory & Ephemeral Expiry (Timeline Memory)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import anyio
import pytest
from starlette.testclient import TestClient

from assistant_core.auth.admin import create_admin_session_token
from assistant_core.config import Settings
from assistant_core.main import create_app
from assistant_core.memory.models import MemoryRecord
from assistant_core.memory.repository import search_explicit_memory
from assistant_core.memory.schemas import ExplicitMemoryCandidate


def test_temporal_candidate_schema() -> None:
    """ExplicitMemoryCandidate supports optional valid_from, expires_at, and temporal_tag."""
    now = datetime.now(UTC)
    expiry = now + timedelta(days=7)

    # Permanent candidate
    c_perm = ExplicitMemoryCandidate(
        key="profile.editor",
        category="preference",
        statement="User prefers Neovim",
        evidence_quote="I prefer Neovim",
    )
    assert c_perm.valid_from is None
    assert c_perm.expires_at is None
    assert c_perm.temporal_tag is None

    # Time-bound candidate
    c_temp = ExplicitMemoryCandidate(
        key="project.sprint_goal",
        category="project",
        statement="Focusing on auth module sprint until Friday",
        evidence_quote="Focusing on auth module sprint until Friday",
        valid_from=now,
        expires_at=expiry,
        temporal_tag="deadline",
    )
    assert c_temp.valid_from == now
    assert c_temp.expires_at == expiry
    assert c_temp.temporal_tag == "deadline"


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
        return self._scalars if self._scalars else self._rows

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


def test_search_explicit_memory_filtering() -> None:
    """Active search only returns unexpired, currently-valid records."""
    now = datetime.now(UTC)
    user_id = uuid.uuid4()

    valid_record = MemoryRecord(
        id=uuid.uuid4(),
        user_id=user_id,
        key="pref.theme",
        category="preference",
        statement="Dark theme preferred",
        kind="explicit",
        confidence=1,
        state="active",
        expires_at=now + timedelta(days=5),
        temporal_tag="temporary_preference",
    )

    session = _FakeSession([_FakeResult(scalars_list=[valid_record])])

    async def exercise() -> None:
        results = await search_explicit_memory(
            session,  # type: ignore[arg-type]
            native_user_id="user-1",
            query="theme",
            limit=5,
        )
        assert len(results) == 1
        assert results[0].statement == "Dark theme preferred"
        assert results[0].temporal_tag == "temporary_preference"

    anyio.run(exercise)


@pytest.fixture
def test_app():
    settings = Settings(
        admin_token="test-secret-token",
        postgres_dsn="postgresql+asyncpg://user:pass@localhost:5432/db",
    )
    return create_app(settings=settings)


@pytest.fixture
def auth_headers():
    token = create_admin_session_token("test-secret-token")
    return {"Cookie": f"assistant_admin_session={token}"}


def test_admin_create_and_list_temporal_memory(test_app, auth_headers) -> None:
    """Admin API creates memory with temporal bounds and returns timeline status."""
    client = TestClient(test_app)
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    exp = now + timedelta(days=3)

    session = _FakeSession(
        results=[
            _FakeResult(scalar=user_id),  # resolve user
        ]
    )
    test_app.state.session_factory = lambda: session

    # 1. Create with expires_at & temporal_tag
    resp = client.post(
        "/v1/admin/memories",
        headers=auth_headers,
        json={
            "native_user_id": "test-user-1",
            "statement": "Working on bio exam prep until Friday",
            "category": "project",
            "confidence": 1.0,
            "expires_at": exp.isoformat(),
            "temporal_tag": "deadline",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["temporal_tag"] == "deadline"
    assert data["expires_at"] is not None

    # 2. List with timeline filter
    mem_id = uuid.uuid4()
    session_list = _FakeSession(
        results=[
            _FakeResult(scalar=1),  # count
            _FakeResult(
                rows=[
                    (
                        mem_id,
                        "Working on bio exam prep until Friday",
                        "project",
                        1,
                        "active",
                        now,
                        None,
                        "test-user-1",
                        None,
                        exp,
                        "deadline",
                    )
                ]
            ),
        ]
    )
    test_app.state.session_factory = lambda: session_list

    list_resp = client.get(
        "/v1/admin/memories?timeline_filter=active_expiring",
        headers=auth_headers,
    )
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["validity_status"] == "active_expiring"
    assert items[0]["temporal_tag"] == "deadline"


def test_admin_update_memory_temporal_bounds(test_app, auth_headers) -> None:
    """Admin API patches expires_at and temporal_tag on memory record."""
    client = TestClient(test_app)
    mem_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(UTC)

    existing_record = MemoryRecord(
        id=mem_id,
        user_id=user_id,
        key="project:123",
        category="project",
        statement="Active project",
        state="active",
        created_at=now,
    )

    session = _FakeSession(
        results=[
            _FakeResult(scalar=existing_record),
        ]
    )
    test_app.state.session_factory = lambda: session

    new_exp = now + timedelta(days=14)
    resp = client.patch(
        f"/v1/admin/memories/{mem_id}",
        headers=auth_headers,
        json={
            "expires_at": new_exp.isoformat(),
            "temporal_tag": "extended_deadline",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["temporal_tag"] == "extended_deadline"
    assert data["expires_at"] is not None
