"""Unit tests for ArtifactRepository using RecordingSession."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from assistant_core.artifacts.models import Artifact, ArtifactVersion
from assistant_core.artifacts.repository import ArtifactRepository
from assistant_core.artifacts.schemas import (
    CreateArtifactRequest,
    ReviseArtifactRequest,
    SheetSpec,
    WorkbookSpec,
)
from assistant_core.artifacts.storage import LocalStorageBackend
from assistant_core.identity.models import UserIdentity


class ScalarResult:
    def __init__(self, value: Any = None, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self.value = value
        self.rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalars(self) -> "ScalarResult":
        return self

    def all(self) -> list[Any]:
        return self.rows


class RecordingSession:
    def __init__(self, values: list[Any]) -> None:
        self.values = values
        self.statements: list[object] = []
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: object, attributes: list[str]) -> None:
        if isinstance(obj, Artifact) and not hasattr(obj, "versions"):
            obj.versions = []

    async def execute(self, statement: object) -> ScalarResult:
        self.statements.append(statement)
        val = self.values.pop(0) if self.values else None
        if isinstance(val, list):
            return ScalarResult(rows=val)
        return ScalarResult(value=val)


@pytest.mark.anyio
async def test_artifact_repository_create_and_revise(tmp_path: Path) -> None:
    user = UserIdentity(id=uuid.uuid4(), native_user_id="user-123")
    storage = LocalStorageBackend(base_dir=str(tmp_path))

    # 1. Test Create
    session = RecordingSession(values=[user])  # 1: get_or_create_user
    repo = ArtifactRepository(session, storage)  # type: ignore[arg-type]

    create_req = CreateArtifactRequest(
        native_user_id="user-123",
        title="Q3 Model",
        artifact_type="xlsx",
        workbook_spec=WorkbookSpec(
            title="Q3 Model",
            sheets=[
                SheetSpec(
                    name="Data", headers=["Item", "Cost"], rows=[["Servers", 500]], totals_row=True
                )
            ],
        ),
        change_summary="Initial commit",
    )
    art, ver1 = await repo.create_artifact(create_req)
    assert art.title == "Q3 Model"
    assert art.current_version_num == 1
    assert ver1.version_num == 1
    assert len(ver1.binary_data) > 0
    if ver1.storage_path:
        assert Path(ver1.storage_path).exists()

    # 2. Test Revise
    art.versions = [ver1]
    session_revise = RecordingSession(values=[art, user])
    repo_revise = ArtifactRepository(session_revise, storage)  # type: ignore[arg-type]

    rev_req = ReviseArtifactRequest(
        native_user_id="user-123",
        workbook_spec=WorkbookSpec(
            title="Q3 Model v2",
            sheets=[
                SheetSpec(
                    name="Data",
                    headers=["Item", "Cost"],
                    rows=[["Servers", 500], ["DB", 300]],
                    totals_row=True,
                )
            ],
        ),
        change_summary="Added DB",
    )
    art_v2, ver2 = await repo_revise.add_version(art.id, rev_req)
    assert art_v2.current_version_num == 2
    assert ver2.version_num == 2
    assert len(ver2.binary_data) > 0
    if ver2.storage_path:
        assert Path(ver2.storage_path).exists()


@pytest.mark.anyio
async def test_artifact_repository_create_pdf(tmp_path: Path) -> None:
    from assistant_core.artifacts.schemas import DocumentSectionSpec, DocumentSpec

    user = UserIdentity(id=uuid.uuid4(), native_user_id="user-123")
    storage = LocalStorageBackend(base_dir=str(tmp_path))

    session = RecordingSession(values=[user])
    repo = ArtifactRepository(session, storage)  # type: ignore[arg-type]

    create_req = CreateArtifactRequest(
        native_user_id="user-123",
        title="Architecture Decision Record",
        artifact_type="pdf",
        document_spec=DocumentSpec(
            title="Architecture Decision Record",
            sections=[
                DocumentSectionSpec(
                    heading="Section 1",
                    paragraphs=["Paragraph text"],
                )
            ],
        ),
        change_summary="Initial PDF creation",
    )
    art, ver1 = await repo.create_artifact(create_req)
    assert art.title == "Architecture Decision Record"
    assert art.artifact_type == "pdf"
    assert ver1.binary_data.startswith(b"%PDF-")
    assert ver1.mime_type == "application/pdf"


@pytest.mark.anyio
async def test_artifact_repository_create_typst(tmp_path: Path) -> None:
    user = UserIdentity(id=uuid.uuid4(), native_user_id="user-123")
    storage = LocalStorageBackend(base_dir=str(tmp_path))

    session = RecordingSession(values=[user])
    repo = ArtifactRepository(session, storage)  # type: ignore[arg-type]

    create_req = CreateArtifactRequest(
        native_user_id="user-123",
        title="Typst Research Notes",
        artifact_type="typst",
        raw_content='#set page(paper: "a4")\n= Research Notes\n\nContent compiled via Typst.',
        change_summary="Initial Typst creation",
    )
    art, ver1 = await repo.create_artifact(create_req)
    assert art.title == "Typst Research Notes"
    assert art.artifact_type == "typst"
    assert ver1.binary_data.startswith(b"%PDF-")
    assert ver1.mime_type == "application/pdf"


@pytest.mark.anyio
async def test_artifact_repository_create_resume(tmp_path: Path) -> None:
    from assistant_core.artifacts.schemas import ExperienceItem, ResumeSpec

    user = UserIdentity(id=uuid.uuid4(), native_user_id="user-123")
    storage = LocalStorageBackend(base_dir=str(tmp_path))

    session = RecordingSession(values=[user])
    repo = ArtifactRepository(session, storage)  # type: ignore[arg-type]

    create_req = CreateArtifactRequest(
        native_user_id="user-123",
        title="Alex Mercer Resume",
        artifact_type="resume",
        resume_spec=ResumeSpec(
            name="Alex Mercer",
            title="Senior Engineer",
            summary="Distributed systems specialist.",
            experience=[
                ExperienceItem(
                    company="Tech Corp",
                    position="Lead Architect",
                    start_date="2020",
                    highlights=["Built cloud native pipelines"],
                )
            ],
        ),
        change_summary="Initial Resume creation",
    )
    art, ver1 = await repo.create_artifact(create_req)
    assert art.title == "Alex Mercer Resume"
    assert art.artifact_type == "resume"
    assert ver1.binary_data.startswith(b"%PDF-")
    assert ver1.mime_type == "application/pdf"


@pytest.mark.anyio
async def test_artifact_repository_revert_to_version(tmp_path: Path) -> None:
    user = UserIdentity(id=uuid.uuid4(), native_user_id="user-123")
    storage = LocalStorageBackend(base_dir=str(tmp_path))
    art_id = uuid.uuid4()

    art = Artifact(
        id=art_id,
        user_id=user.id,
        title="Doc to Revert",
        slug="doc-to-revert",
        artifact_type="markdown",
        current_version_num=2,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    v1 = ArtifactVersion(
        id=uuid.uuid4(),
        artifact_id=art_id,
        version_num=1,
        binary_data=b"# V1 Content",
        content_sha256="h1",
        file_size_bytes=12,
        mime_type="text/markdown",
        change_summary="v1",
        created_at=datetime.now(UTC),
    )
    v2 = ArtifactVersion(
        id=uuid.uuid4(),
        artifact_id=art_id,
        version_num=2,
        binary_data=b"# V2 Content",
        content_sha256="h2",
        file_size_bytes=12,
        mime_type="text/markdown",
        change_summary="v2",
        created_at=datetime.now(UTC),
    )
    art.versions = [v1, v2]

    session = RecordingSession(values=[art])
    repo = ArtifactRepository(session, storage)  # type: ignore[arg-type]

    reverted_art, v3 = await repo.revert_to_version(art_id, 1, user_id=user.id)
    assert reverted_art.current_version_num == 3
    assert v3.version_num == 3
    assert v3.binary_data == b"# V1 Content"
    assert v3.change_summary == "Reverted to v1"
    if v3.storage_path:
        assert Path(v3.storage_path).exists()

    # Revert to non-existent version raises ValueError
    session2 = RecordingSession(values=[art])
    repo2 = ArtifactRepository(session2, storage)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Version 99 not found"):
        await repo2.revert_to_version(art_id, 99, user_id=user.id)

    # Revert with wrong user raises PermissionError
    session3 = RecordingSession(values=[art])
    repo3 = ArtifactRepository(session3, storage)  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="does not own"):
        await repo3.revert_to_version(art_id, 1, user_id=uuid.uuid4())


@pytest.mark.anyio
async def test_artifact_repository_diff_extraction() -> None:
    from assistant_core.artifacts.generators.docx_gen import DocxGenerator
    from assistant_core.artifacts.generators.xlsx_gen import XlsxGenerator
    from assistant_core.artifacts.repository import compute_unified_diff, extract_artifact_text
    from assistant_core.artifacts.schemas import (
        DocumentSectionSpec,
        DocumentSpec,
        SheetSpec,
        TableSpec,
        WorkbookSpec,
    )

    # 1. Plain Text / Markdown
    lines1 = extract_artifact_text("markdown", b"Hello\nWorld")
    lines2 = extract_artifact_text("markdown", b"Hello\nNew World\nExtra")
    diff = compute_unified_diff(lines1, lines2, 1, 2)
    assert diff["v1"] == 1
    assert diff["v2"] == 2
    assert diff["additions"] == 2
    assert diff["deletions"] == 1

    # 2. DOCX text extraction
    doc_bytes1 = DocxGenerator.generate(
        DocumentSpec(
            title="Report",
            sections=[
                DocumentSectionSpec(
                    heading="Intro",
                    paragraphs=["Paragraph 1"],
                    table=TableSpec(headers=["Col1", "Col2"], rows=[["Val1", "Val2"]]),
                )
            ],
        )
    )
    doc_bytes2 = DocxGenerator.generate(
        DocumentSpec(
            title="Report",
            sections=[
                DocumentSectionSpec(
                    heading="Intro",
                    paragraphs=["Paragraph 1 Modified"],
                    table=TableSpec(headers=["Col1", "Col2"], rows=[["Val1", "Val2 Updated"]]),
                )
            ],
        )
    )
    docx_lines1 = extract_artifact_text("docx", doc_bytes1)
    docx_lines2 = extract_artifact_text("docx", doc_bytes2)
    assert any("Paragraph 1" in line for line in docx_lines1)
    assert any("Val1 | Val2" in line for line in docx_lines1)
    docx_diff = compute_unified_diff(docx_lines1, docx_lines2, 1, 2)
    assert docx_diff["additions"] >= 1
    assert docx_diff["deletions"] >= 1

    # 3. XLSX text extraction
    wb_bytes1 = XlsxGenerator.generate(
        WorkbookSpec(
            title="Finance",
            sheets=[SheetSpec(name="S1", headers=["Item", "Cost"], rows=[["Servers", 500]])],
        )
    )
    wb_bytes2 = XlsxGenerator.generate(
        WorkbookSpec(
            title="Finance",
            sheets=[
                SheetSpec(
                    name="S1",
                    headers=["Item", "Cost"],
                    rows=[["Servers", 500], ["Databases", 300]],
                )
            ],
        )
    )
    xlsx_lines1 = extract_artifact_text("xlsx", wb_bytes1)
    xlsx_lines2 = extract_artifact_text("xlsx", wb_bytes2)
    assert any("Servers" in line for line in xlsx_lines1)
    xlsx_diff = compute_unified_diff(xlsx_lines1, xlsx_lines2, 1, 2)
    assert xlsx_diff["additions"] >= 1
