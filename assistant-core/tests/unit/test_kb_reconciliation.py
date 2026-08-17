"""Unit tests for Knowledge Base document reconciliation, hash deduplication, and audit logs."""

import uuid
from datetime import UTC, datetime
from typing import Any, Self
from unittest.mock import patch

from starlette.testclient import TestClient

from assistant_core.auth.admin import create_admin_session_token
from assistant_core.config import Settings
from assistant_core.files.models import KBReconciliationRun
from assistant_core.files.repository import reconcile_and_log_kb_documents
from assistant_core.main import create_app


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
        self.rowcount = len(self._scalars) if self._scalars else len(self._rows)

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


def test_reconcile_and_log_kb_documents_prunes_matching_hashes_and_ids() -> None:
    """reconcile_and_log_kb_documents identifies KB file IDs and hashes and records an audit log."""
    doc1_id = uuid.uuid4()
    doc2_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Doc 1: matches KB file ID 'kb-file-1'
    # Doc 2: matches KB content hash 'sha256-hash-2'
    # Doc 3: manual user chat upload, no match -> preserved
    doc3_id = uuid.uuid4()

    mock_docs_rows = [
        (doc1_id, "kb-file-1", "Coin Change.md", "sha256-hash-1", user_id),
        (doc2_id, "file-abc", "Decode Ways.md", "sha256-hash-2", user_id),
        (doc3_id, "user-upload-99", "custom_notes.txt", "sha256-hash-3", user_id),
    ]

    fake_session = _FakeSession(
        [
            _FakeResult(rows=mock_docs_rows),  # doc_stmt query
            _FakeResult(),  # delete FileReference
            _FakeResult(),  # delete FileDocument
            _FakeResult(),  # delete orphan FileSegment
        ]
    )

    with patch(
        "assistant_core.files.repository.fetch_all_kb_metadata_and_hashes",
        return_value=(
            {"kb-file-1"},  # kb_file_ids
            {"sha256-hash-2"},  # kb_hashes
            {"Coin Change.md"},  # kb_filenames
        ),
    ):
        import asyncio

        run = asyncio.run(
            reconcile_and_log_kb_documents(
                fake_session,
                base_url="http://open-webui:8080",
                api_key="test-token",
                trigger="manual_admin",
            )
        )

        assert run.status == "success"
        assert run.trigger == "manual_admin"
        assert run.kb_files_scanned == 1
        assert run.pruned_count == 2
        assert len(run.details) == 2
        assert run.details[0]["native_file_id"] == "kb-file-1"
        assert run.details[0]["match_type"] == "kb_file_id"
        assert run.details[1]["native_file_id"] == "file-abc"
        assert run.details[1]["match_type"] == "kb_content_sha256"
        assert fake_session.committed is True
        assert len(fake_session.added) == 1
        assert isinstance(fake_session.added[0], KBReconciliationRun)


def test_reconcile_and_log_kb_documents_no_changes() -> None:
    """When no KB files match existing documents, records a clean no_changes audit log."""
    user_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    mock_docs_rows = [
        (doc_id, "user-upload-1", "user_report.pdf", "unique-sha256", user_id),
    ]

    fake_session = _FakeSession(
        [
            _FakeResult(rows=mock_docs_rows),
        ]
    )

    with patch(
        "assistant_core.files.repository.fetch_all_kb_metadata_and_hashes",
        return_value=(
            {"kb-file-99"},
            {"other-sha256"},
            {"other-file.md"},
        ),
    ):
        import asyncio

        run = asyncio.run(
            reconcile_and_log_kb_documents(
                fake_session,
                base_url="http://open-webui:8080",
                api_key=None,
                trigger="worker_hourly",
            )
        )

        assert run.status == "no_changes"
        assert run.trigger == "worker_hourly"
        assert run.pruned_count == 0
        assert run.details == []
        assert fake_session.committed is True


def test_list_kb_reconciliation_runs_endpoint() -> None:
    """GET /v1/admin/files/reconcile-kb/runs returns paginated history with details."""
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_run = KBReconciliationRun(
        id=run_id,
        trigger="manual_admin",
        status="success",
        kb_files_scanned=12,
        pruned_count=1,
        details=[
            {
                "native_file_id": "kb-123",
                "filename": "Coin Change.md",
                "content_sha256": "abc",
                "match_type": "kb_file_id",
                "reason": "Document matched active Knowledge Base",
            }
        ],
        duration_ms=250.0,
        created_at=now,
    )

    fake_session = _FakeSession(
        [
            _FakeResult(scalar=1),  # count
            _FakeResult(scalars_list=[mock_run]),  # runs
        ]
    )

    client = _make_test_client(fake_session)
    resp = client.get("/v1/admin/files/reconcile-kb/runs")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == str(run_id)
    assert data["items"][0]["pruned_count"] == 1
    assert data["items"][0]["details"][0]["filename"] == "Coin Change.md"


def test_get_kb_reconciliation_run_detail_and_not_found() -> None:
    """GET /v1/admin/files/reconcile-kb/runs/{id} returns detail or 404."""
    run_id = uuid.uuid4()
    mock_run = KBReconciliationRun(
        id=run_id,
        trigger="worker_hourly",
        status="no_changes",
        kb_files_scanned=5,
        pruned_count=0,
        details=[],
        duration_ms=80.0,
        created_at=datetime.now(UTC),
    )

    # 1. Found
    fake_session = _FakeSession([_FakeResult(scalar=mock_run)])
    client = _make_test_client(fake_session)
    resp = client.get(f"/v1/admin/files/reconcile-kb/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(run_id)

    # 2. Not Found
    fake_session_empty = _FakeSession([_FakeResult(scalar=None)])
    client_empty = _make_test_client(fake_session_empty)
    resp_404 = client_empty.get(f"/v1/admin/files/reconcile-kb/runs/{uuid.uuid4()}")
    assert resp_404.status_code == 404


def test_fetch_all_kb_metadata_and_hashes_discovers_oikb_files() -> None:
    """fetch_all_kb_metadata_and_hashes retrieves files directly from oikb sync endpoints."""
    import httpx

    from assistant_core.files.client import fetch_all_kb_metadata_and_hashes

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/api/v1/knowledge/" in url_str:
            return httpx.Response(200, json=[])
        if "/api/v1/files/" in url_str:
            return httpx.Response(200, json=[])
        if "/sync/history" in url_str or "/history" in url_str:
            return httpx.Response(
                200,
                json=[
                    {
                        "file_path": "10_LeetCode/Coin Change.md",
                        "content_hash": "sha256-coin-change",
                        "file_id": "oikb-file-1",
                    }
                ],
            )
        return httpx.Response(404)

    real_client_cls = httpx.Client
    transport = httpx.MockTransport(mock_handler)
    with patch(
        "assistant_core.files.client.httpx.Client",
        side_effect=lambda **kwargs: real_client_cls(transport=transport, timeout=kwargs.get("timeout")),
    ):
        fids, hashes, fnames = fetch_all_kb_metadata_and_hashes(
            base_url="http://open-webui:8080",
            api_key=None,
            oikb_url="http://oikb:8080",
        )

        assert "oikb-file-1" in fids
        assert "sha256-coin-change" in hashes
        assert "Coin Change.md" in fnames
        assert "10_LeetCode/Coin Change.md" in fnames
