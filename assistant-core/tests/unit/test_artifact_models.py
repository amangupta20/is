"""Unit tests for Artifact and OnlyOffice database models."""

import uuid
from datetime import UTC, datetime

from assistant_core.artifacts.models import Artifact, ArtifactVersion, OnlyOfficeSession


def test_artifact_models_instantiation() -> None:
    user_id = uuid.uuid4()
    art_id = uuid.uuid4()
    turn_id = uuid.uuid4()

    artifact = Artifact(
        id=art_id,
        user_id=user_id,
        title="Q3 Financial Model",
        slug="q3-financial-model",
        artifact_type="xlsx",
        current_version_num=1,
    )
    assert artifact.id == art_id
    assert artifact.title == "Q3 Financial Model"
    assert artifact.artifact_type == "xlsx"
    assert artifact.current_version_num == 1

    version = ArtifactVersion(
        id=uuid.uuid4(),
        artifact_id=art_id,
        version_num=1,
        binary_data=b"test xlsx binary",
        content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        storage_path="/data/artifacts/user-1/art-1/v1.xlsx",
        file_size_bytes=45120,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        change_summary="Initial spreadsheet creation",
        created_by_turn_id=turn_id,
    )
    assert version.artifact_id == art_id
    assert version.binary_data == b"test xlsx binary"
    assert version.version_num == 1
    assert version.file_size_bytes == 45120

    session = OnlyOfficeSession(
        id=uuid.uuid4(),
        artifact_id=art_id,
        user_id=user_id,
        session_key="session-key-12345",
        status="active",
        expires_at=datetime.now(UTC),
    )
    assert session.session_key == "session-key-12345"
    assert session.status == "active"
