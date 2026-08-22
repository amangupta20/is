"""Unit and API tests for artifacts endpoints."""

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from starlette.testclient import TestClient

from assistant_core.artifacts.generators.xlsx_gen import XlsxGenerator
from assistant_core.artifacts.models import Artifact, ArtifactVersion
from assistant_core.artifacts.schemas import SheetSpec, WorkbookSpec
from assistant_core.artifacts.storage import LocalStorageBackend
from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.identity.models import UserIdentity
from assistant_core.main import create_app


class _FakeResult:
    def __init__(self, value: Any = None, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self.value = value
        self.rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalar_one(self) -> Any:
        return self.value

    def scalars(self) -> Self:
        return self

    def all(self) -> list[Any]:
        return self.rows


class _FakeSession:
    def __init__(self, values: list[Any]) -> None:
        self.values = values
        self.statements: list[object] = []
        self.added: list[object] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def refresh(self, obj: object, attributes: list[str]) -> None:
        if isinstance(obj, Artifact) and not hasattr(obj, "versions"):
            obj.versions = []

    async def execute(self, statement: object) -> _FakeResult:
        self.statements.append(statement)
        val = self.values.pop(0) if self.values else None
        if isinstance(val, list):
            return _FakeResult(rows=val)
        return _FakeResult(value=val)


def _signed_headers(secret: str, method: str, path: str, body: bytes) -> dict[str, str]:
    now = str(int(time.time()))
    sig = sign_request(secret, method, path, now, body)
    return {
        "x-assistant-timestamp": now,
        "x-assistant-signature": sig,
    }


def test_artifacts_api_create_and_download(tmp_path: Path) -> None:
    secret = "test-hmac-secret-at-least-32-chars-long"
    settings = Settings(
        hmac_secret=secret,
        artifacts_dir=str(tmp_path),
        onlyoffice_url="https://onlyoffice.test",
        onlyoffice_jwt_secret="test-jwt-secret-12345",
    )
    app = create_app(settings)
    client = TestClient(app)

    user = UserIdentity(id=uuid.uuid4(), native_user_id="user-123")
    session = _FakeSession([user])
    app.state.session_factory = lambda: session

    # 1. Create Spreadsheet
    create_payload = {
        "native_user_id": "user-123",
        "title": "Quarterly P&L",
        "artifact_type": "xlsx",
        "workbook_spec": {
            "title": "Quarterly P&L",
            "sheets": [
                {
                    "name": "PnL",
                    "headers": ["Item", "Amount"],
                    "rows": [["Cloud Hosting", 450.0]],
                    "column_types": ["text", "currency"],
                    "totals_row": True,
                }
            ],
        },
        "change_summary": "Initial P&L",
    }
    body_bytes = json.dumps(create_payload, separators=(",", ":")).encode()
    headers = _signed_headers(secret, "POST", "/v1/artifacts/create", body_bytes)
    headers["content-type"] = "application/json"

    resp = client.post("/v1/artifacts/create", content=body_bytes, headers=headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["title"] == "Quarterly P&L"
    assert data["current_version_num"] == 1
    artifact_id = data["id"]

    # 2. Download File
    # Prepare artifact with real saved file for download test
    storage = LocalStorageBackend(base_dir=str(tmp_path))
    art_uuid = uuid.UUID(artifact_id)
    raw_xlsx = XlsxGenerator.generate(
        WorkbookSpec(
            title="Quarterly P&L",
            sheets=[SheetSpec(name="PnL", headers=["A"], rows=[[1]])],
        )
    )
    s_path, c_hash, f_size = storage.save(user.id, art_uuid, 1, "xlsx", raw_xlsx)

    persisted_art = Artifact(
        id=art_uuid,
        user_id=user.id,
        title="Quarterly P&L",
        slug="quarterly-pl",
        artifact_type="xlsx",
        current_version_num=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    persisted_ver = ArtifactVersion(
        id=uuid.uuid4(),
        artifact_id=art_uuid,
        version_num=1,
        binary_data=raw_xlsx,
        content_sha256=c_hash,
        storage_path=s_path,
        file_size_bytes=f_size,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        change_summary="Initial",
        created_at=datetime.now(UTC),
    )
    persisted_art.versions = [persisted_ver]

    dl_session = _FakeSession([persisted_art])
    app.state.session_factory = lambda: dl_session

    dl_resp = client.get(f"/v1/artifacts/{artifact_id}/download")
    assert dl_resp.status_code == 200
    assert "spreadsheetml" in dl_resp.headers["content-type"]
    assert len(dl_resp.content) > 0


def test_artifacts_onlyoffice_session_and_callback(tmp_path: Path) -> None:
    secret = "test-hmac-secret-at-least-32-chars-long"
    settings = Settings(
        hmac_secret=secret,
        artifacts_dir=str(tmp_path),
        onlyoffice_url="https://onlyoffice.test",
        onlyoffice_jwt_secret="test-jwt-secret-at-least-32-chars-long",
    )
    app = create_app(settings)
    client = TestClient(app)

    user = UserIdentity(id=uuid.uuid4(), native_user_id="user-123")
    art_id = uuid.uuid4()
    art = Artifact(
        id=art_id,
        user_id=user.id,
        title="Document 1",
        slug="document-1",
        artifact_type="docx",
        current_version_num=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    ver = ArtifactVersion(
        id=uuid.uuid4(),
        artifact_id=art_id,
        version_num=1,
        binary_data=b"docx data",
        content_sha256="abc",
        storage_path=str(tmp_path / "v1.docx"),
        file_size_bytes=100,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        change_summary="Initial",
        created_at=datetime.now(UTC),
    )
    art.versions = [ver]

    # Create OnlyOffice session
    session = _FakeSession([art, user])
    app.state.session_factory = lambda: session

    headers = _signed_headers(secret, "POST", f"/v1/artifacts/{art_id}/onlyoffice/session", b"")
    resp = client.post(
        f"/v1/artifacts/{art_id}/onlyoffice/session?native_user_id=user-123", headers=headers
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["onlyoffice_url"] == "https://onlyoffice.test"
    assert "token" in res_data["config"]


def test_create_presentation_api() -> None:
    secret = "test-hmac-secret-at-least-32-chars-long"
    settings = Settings(hmac_secret=secret)
    app = create_app(settings)
    client = TestClient(app)

    user = UserIdentity(id=uuid.uuid4(), native_user_id="user-123")
    session = _FakeSession([user])
    app.state.session_factory = lambda: session

    payload = {
        "native_user_id": "user-123",
        "title": "Test Presentation Deck",
        "artifact_type": "pptx",
        "presentation_spec": {
            "title": "Test Presentation Deck",
            "subtitle": "Automated Deck Generation & Feature Verification",
            "slides": [
                {
                    "title": "Executive Summary",
                    "layout": "bullets",
                    "bullets": [
                        "Overview of automated presentation generation",
                        "Key validation goals",
                    ],
                },
                {
                    "title": "Project Objectives",
                    "layout": "bullets",
                    "bullets": ["Validate slide deck pipeline", "Clean visual hierarchy"],
                },
            ],
        },
        "change_summary": "Generated presentation",
    }
    body_bytes = json.dumps(payload, separators=(",", ":")).encode()
    headers = _signed_headers(secret, "POST", "/v1/artifacts/create", body_bytes)
    headers["content-type"] = "application/json"

    resp = client.post("/v1/artifacts/create", content=body_bytes, headers=headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["title"] == "Test Presentation Deck"
    assert data["artifact_type"] == "pptx"
    assert data["current_version_num"] == 1
    assert "download_url" in data
