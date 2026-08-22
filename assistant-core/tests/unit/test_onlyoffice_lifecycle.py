"""Unit tests for OnlyOffice JWT verification and callback lifecycle."""

import uuid
from datetime import UTC, datetime
from typing import Any, Self
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from starlette.testclient import TestClient

from assistant_core.artifacts.models import Artifact, ArtifactVersion, OnlyOfficeSession
from assistant_core.artifacts.onlyoffice import OnlyOfficeManager
from assistant_core.artifacts.repository import ArtifactRepository
from assistant_core.config import Settings
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


@pytest.mark.anyio
async def test_onlyoffice_jwt_signature_validation() -> None:
    secret = "valid-secret-key-1234567890-test-32bytes"
    settings = Settings(
        hmac_secret="test-hmac-secret-at-least-32-chars-long",
        onlyoffice_jwt_secret=secret,
    )
    session = _FakeSession([])
    repo = ArtifactRepository(session)  # type: ignore[arg-type]
    manager = OnlyOfficeManager(session, repo, settings)  # type: ignore[arg-type]

    raw_payload = {"status": 1, "key": "sess-key-123"}
    valid_token = jwt.encode(raw_payload, secret, algorithm="HS256")
    forged_token = jwt.encode(
        raw_payload, "wrong-forged-secret-key-at-least-32chars", algorithm="HS256"
    )

    # 1. Direct manager verification
    verified = manager.verify_callback_jwt(valid_token)
    assert verified["status"] == 1
    assert verified["key"] == "sess-key-123"

    with pytest.raises(jwt.PyJWTError):
        manager.verify_callback_jwt(forged_token)

    # 2. Wrapped payload format verification
    wrapped_token = jwt.encode({"payload": raw_payload}, secret, algorithm="HS256")
    verified_wrapped = manager.verify_callback_jwt(wrapped_token)
    assert verified_wrapped["status"] == 1
    assert verified_wrapped["key"] == "sess-key-123"

    # 3. HTTP API endpoint verification with Authorization header & payload token
    app = create_app(settings)
    client = TestClient(app)

    art_id = uuid.uuid4()
    user_id = uuid.uuid4()
    oo_sess = OnlyOfficeSession(
        id=uuid.uuid4(),
        artifact_id=art_id,
        user_id=user_id,
        session_key="sess-key-123",
        status="active",
        expires_at=datetime.now(UTC),
    )

    # Valid Bearer header
    app.state.session_factory = lambda: _FakeSession([oo_sess])
    resp = client.post(
        f"/v1/artifacts/{art_id}/onlyoffice/callback?key=sess-key-123",
        json={"status": 1},
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["error"] == 0

    # Valid token in JSON payload
    app.state.session_factory = lambda: _FakeSession([oo_sess])
    resp = client.post(
        f"/v1/artifacts/{art_id}/onlyoffice/callback?key=sess-key-123",
        json={"token": valid_token},
    )
    assert resp.status_code == 200
    assert resp.json()["error"] == 0

    # Forged Bearer header -> 401 Unauthorized
    resp = client.post(
        f"/v1/artifacts/{art_id}/onlyoffice/callback?key=sess-key-123",
        json={"status": 1},
        headers={"Authorization": f"Bearer {forged_token}"},
    )
    assert resp.status_code == 401

    # Forged token in JSON payload -> 401 Unauthorized
    resp = client.post(
        f"/v1/artifacts/{art_id}/onlyoffice/callback?key=sess-key-123",
        json={"token": forged_token},
    )
    assert resp.status_code == 401

    # Missing token when secret is configured -> 401 Unauthorized
    resp = client.post(
        f"/v1/artifacts/{art_id}/onlyoffice/callback?key=sess-key-123",
        json={"status": 1},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_onlyoffice_status_editing_and_closed() -> None:
    settings = Settings(hmac_secret="test-hmac-secret-at-least-32-chars-long")
    art_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Test status=1 (Editing)
    oo_sess1 = OnlyOfficeSession(
        id=uuid.uuid4(),
        artifact_id=art_id,
        user_id=user_id,
        session_key="sess-key-1",
        status="active",
        expires_at=datetime.now(UTC),
    )
    session1 = _FakeSession([oo_sess1])
    repo1 = ArtifactRepository(session1)  # type: ignore[arg-type]
    manager1 = OnlyOfficeManager(session1, repo1, settings)  # type: ignore[arg-type]

    res1 = await manager1.handle_callback(
        artifact_id=art_id,
        session_key="sess-key-1",
        payload={"status": 1},
    )
    assert res1["error"] == 0
    assert oo_sess1.status == "editing"

    # Test status=4 (Closed without changes)
    oo_sess2 = OnlyOfficeSession(
        id=uuid.uuid4(),
        artifact_id=art_id,
        user_id=user_id,
        session_key="sess-key-2",
        status="editing",
        expires_at=datetime.now(UTC),
    )
    session2 = _FakeSession([oo_sess2])
    repo2 = ArtifactRepository(session2)  # type: ignore[arg-type]
    manager2 = OnlyOfficeManager(session2, repo2, settings)  # type: ignore[arg-type]

    res2 = await manager2.handle_callback(
        artifact_id=art_id,
        session_key="sess-key-2",
        payload={"status": 4},
    )
    assert res2["error"] == 0
    assert oo_sess2.status == "closed"


@pytest.mark.anyio
async def test_onlyoffice_status_save_creates_version() -> None:
    settings = Settings(hmac_secret="test-hmac-secret-at-least-32-chars-long")
    art_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Mock download response from Document Server
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.content = b"newly-edited-docx-bytes"

    # 1. Test status=2 (Ready for saving)
    oo_sess1 = OnlyOfficeSession(
        id=uuid.uuid4(),
        artifact_id=art_id,
        user_id=user_id,
        session_key="sess-key-save",
        status="editing",
        expires_at=datetime.now(UTC),
    )
    art1 = Artifact(
        id=art_id,
        user_id=user_id,
        title="Document",
        slug="document",
        artifact_type="docx",
        current_version_num=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    ver1 = ArtifactVersion(
        id=uuid.uuid4(),
        artifact_id=art_id,
        version_num=1,
        binary_data=b"original-docx",
        content_sha256="abc",
        file_size_bytes=13,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        change_summary="Original",
        created_at=datetime.now(UTC),
    )
    art1.versions = [ver1]

    session1 = _FakeSession([oo_sess1, art1])
    repo1 = ArtifactRepository(session1)  # type: ignore[arg-type]
    manager1 = OnlyOfficeManager(session1, repo1, settings)  # type: ignore[arg-type]

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res1 = await manager1.handle_callback(
            artifact_id=art_id,
            session_key="sess-key-save",
            payload={"status": 2, "url": "https://onlyoffice.test/download/doc2.docx"},
        )

    assert res1["error"] == 0
    assert res1["saved_version"] == 2
    assert oo_sess1.status == "saved"
    assert art1.current_version_num == 2

    # 2. Test status=6 (Force save)
    oo_sess2 = OnlyOfficeSession(
        id=uuid.uuid4(),
        artifact_id=art_id,
        user_id=user_id,
        session_key="sess-key-forcesave",
        status="editing",
        expires_at=datetime.now(UTC),
    )
    session2 = _FakeSession([oo_sess2, art1])
    repo2 = ArtifactRepository(session2)  # type: ignore[arg-type]
    manager2 = OnlyOfficeManager(session2, repo2, settings)  # type: ignore[arg-type]

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res2 = await manager2.handle_callback(
            artifact_id=art_id,
            session_key="sess-key-forcesave",
            payload={"status": 6, "url": "https://onlyoffice.test/download/doc3.docx"},
        )

    assert res2["error"] == 0
    assert res2["saved_version"] == 3
    assert oo_sess2.status == "saved"
    assert art1.current_version_num == 3


@pytest.mark.anyio
async def test_onlyoffice_status_error_is_non_destructive() -> None:
    settings = Settings(hmac_secret="test-hmac-secret-at-least-32-chars-long")
    art_id = uuid.uuid4()
    user_id = uuid.uuid4()

    oo_sess = OnlyOfficeSession(
        id=uuid.uuid4(),
        artifact_id=art_id,
        user_id=user_id,
        session_key="sess-key-err",
        status="editing",
        expires_at=datetime.now(UTC),
    )
    art = Artifact(
        id=art_id,
        user_id=user_id,
        title="Document",
        slug="document",
        artifact_type="docx",
        current_version_num=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    ver1 = ArtifactVersion(
        id=uuid.uuid4(),
        artifact_id=art_id,
        version_num=1,
        binary_data=b"original-docx",
        content_sha256="abc",
        file_size_bytes=13,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        change_summary="Original",
        created_at=datetime.now(UTC),
    )
    art.versions = [ver1]

    session = _FakeSession([oo_sess])
    repo = ArtifactRepository(session)  # type: ignore[arg-type]
    manager = OnlyOfficeManager(session, repo, settings)  # type: ignore[arg-type]

    res = await manager.handle_callback(
        artifact_id=art_id,
        session_key="sess-key-err",
        payload={"status": 3},
    )

    assert res["error"] == 0
    assert oo_sess.status == "error"
    # Ensure current version is untouched
    assert art.current_version_num == 1
    assert len(art.versions) == 1
