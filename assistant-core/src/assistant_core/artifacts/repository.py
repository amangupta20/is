import difflib
import hashlib
import io
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import openpyxl
from docx import Document
from pptx import Presentation
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from assistant_core.artifacts.generators.docx_gen import DocxGenerator
from assistant_core.artifacts.generators.pdf_gen import PdfGenerator
from assistant_core.artifacts.generators.pptx_gen import PptxGenerator
from assistant_core.artifacts.generators.typst_gen import TypstGenerator
from assistant_core.artifacts.generators.xlsx_gen import XlsxGenerator
from assistant_core.artifacts.models import Artifact, ArtifactVersion
from assistant_core.artifacts.schemas import (
    CreateArtifactRequest,
    DocumentSectionSpec,
    DocumentSpec,
    PresentationSpec,
    ResumeSpec,
    ReviseArtifactRequest,
    SheetSpec,
    SlideSpec,
    WorkbookSpec,
)
from assistant_core.artifacts.storage import LocalStorageBackend
from assistant_core.identity.models import UserIdentity

MIME_MAP = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "markdown": "text/markdown",
    "typst": "application/pdf",
    "resume": "application/pdf",
}


def _slugify(title: str) -> str:
    """Sanitize title into URL/filesystem friendly slug."""
    s = title.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-") or "artifact"


class ArtifactRepository:
    """Repository managing artifact lifecycles and version chains."""

    def __init__(self, session: AsyncSession, storage: LocalStorageBackend | None = None) -> None:
        self.session = session
        self.storage = storage

    async def get_or_create_user(self, native_user_id: str) -> UserIdentity:
        """Fetch or create UserIdentity."""
        stmt = select(UserIdentity).where(UserIdentity.native_user_id == native_user_id)
        res = await self.session.execute(stmt)
        user = res.scalar_one_or_none()
        if not user:
            user = UserIdentity(id=uuid.uuid4(), native_user_id=native_user_id)
            self.session.add(user)
            await self.session.flush()
        return user

    async def create_artifact(
        self, request: CreateArtifactRequest
    ) -> tuple[Artifact, ArtifactVersion]:
        """Create a new artifact and record its initial version 1."""
        user = await self.get_or_create_user(request.native_user_id)
        artifact_id = uuid.uuid4()
        slug = _slugify(request.title)

        # 1. Render binary data from specs
        data = self._render_binary(
            artifact_type=request.artifact_type,
            workbook_spec=request.workbook_spec,
            document_spec=request.document_spec,
            presentation_spec=request.presentation_spec,
            resume_spec=request.resume_spec,
            raw_content=request.raw_content,
            title=request.title,
        )

        content_sha256 = hashlib.sha256(data).hexdigest()
        file_size = len(data)
        storage_path = None
        if self.storage is not None:
            storage_path, _, _ = self.storage.save(
                user_id=user.id,
                artifact_id=artifact_id,
                version_num=1,
                ext=request.artifact_type,
                data=data,
            )

        # 2. Create database records
        now = datetime.now(UTC)
        artifact = Artifact(
            id=artifact_id,
            user_id=user.id,
            title=request.title,
            slug=slug,
            artifact_type=request.artifact_type,
            native_project_id=request.native_project_id,
            native_folder_id=request.native_folder_id,
            current_version_num=1,
            created_at=now,
            updated_at=now,
        )
        self.session.add(artifact)

        version = ArtifactVersion(
            id=uuid.uuid4(),
            artifact_id=artifact_id,
            version_num=1,
            binary_data=data,
            content_sha256=content_sha256,
            storage_path=storage_path,
            file_size_bytes=file_size,
            mime_type=MIME_MAP.get(request.artifact_type, "application/octet-stream"),
            change_summary=request.change_summary or "Initial creation",
            created_at=now,
        )
        self.session.add(version)
        await self.session.flush()

        # Eager load versions
        await self.session.refresh(artifact, ["versions"])
        return artifact, version

    async def add_version(
        self,
        artifact_id: uuid.UUID,
        request: ReviseArtifactRequest,
    ) -> tuple[Artifact, ArtifactVersion]:
        """Append an immutable version N+1 to an existing artifact."""
        artifact = await self.get_artifact(artifact_id)
        if not artifact or artifact.tombstoned_at is not None:
            raise ValueError(f"Artifact {artifact_id} not found or tombstoned")

        user = await self.get_or_create_user(request.native_user_id)
        if artifact.user_id != user.id:
            raise PermissionError("User does not own this artifact")

        next_version_num = artifact.current_version_num + 1

        # 1. Render binary data
        data = self._render_binary(
            artifact_type=artifact.artifact_type,
            workbook_spec=request.workbook_spec,
            document_spec=request.document_spec,
            presentation_spec=request.presentation_spec,
            resume_spec=request.resume_spec,
            raw_content=request.raw_content,
            title=artifact.title,
        )

        content_sha256 = hashlib.sha256(data).hexdigest()
        file_size = len(data)
        storage_path = None
        if self.storage is not None:
            storage_path, _, _ = self.storage.save(
                user_id=user.id,
                artifact_id=artifact_id,
                version_num=next_version_num,
                ext=artifact.artifact_type,
                data=data,
            )

        # 2. Create version record
        now = datetime.now(UTC)
        version = ArtifactVersion(
            id=uuid.uuid4(),
            artifact_id=artifact_id,
            version_num=next_version_num,
            binary_data=data,
            content_sha256=content_sha256,
            storage_path=storage_path,
            file_size_bytes=file_size,
            mime_type=MIME_MAP.get(artifact.artifact_type, "application/octet-stream"),
            change_summary=request.change_summary or f"Revision v{next_version_num}",
            created_at=now,
        )
        self.session.add(version)
        artifact.current_version_num = next_version_num
        artifact.updated_at = now
        await self.session.flush()
        await self.session.refresh(artifact, ["versions"])
        return artifact, version

    async def add_raw_binary_version(
        self,
        artifact_id: uuid.UUID,
        user_id: uuid.UUID,
        raw_data: bytes,
        change_summary: str = "OnlyOffice Web Edit",
    ) -> tuple[Artifact, ArtifactVersion]:
        """Save a raw binary received from OnlyOffice callback as version N+1."""
        artifact = await self.get_artifact(artifact_id)
        if not artifact or artifact.tombstoned_at is not None:
            raise ValueError(f"Artifact {artifact_id} not found or tombstoned")

        if artifact.user_id != user_id:
            raise PermissionError("User does not own this artifact")

        next_version_num = artifact.current_version_num + 1

        content_sha256 = hashlib.sha256(raw_data).hexdigest()
        file_size = len(raw_data)
        storage_path = None
        if self.storage is not None:
            storage_path, _, _ = self.storage.save(
                user_id=user_id,
                artifact_id=artifact_id,
                version_num=next_version_num,
                ext=artifact.artifact_type,
                data=raw_data,
            )

        now = datetime.now(UTC)
        version = ArtifactVersion(
            id=uuid.uuid4(),
            artifact_id=artifact_id,
            version_num=next_version_num,
            binary_data=raw_data,
            content_sha256=content_sha256,
            storage_path=storage_path,
            file_size_bytes=file_size,
            mime_type=MIME_MAP.get(artifact.artifact_type, "application/octet-stream"),
            change_summary=change_summary,
            created_at=now,
        )
        self.session.add(version)
        artifact.current_version_num = next_version_num
        artifact.updated_at = now
        await self.session.flush()
        await self.session.refresh(artifact, ["versions"])
        return artifact, version

    async def get_artifact(self, artifact_id: uuid.UUID) -> Artifact | None:
        """Fetch artifact with full version chain."""
        stmt = (
            select(Artifact)
            .where(Artifact.id == artifact_id)
            .options(selectinload(Artifact.versions))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_user_artifacts(
        self,
        native_user_id: str,
        limit: int = 50,
        offset: int = 0,
        artifact_type: str | None = None,
    ) -> list[Artifact]:
        """List active artifacts for a user."""
        user = await self.get_or_create_user(native_user_id)
        stmt = (
            select(Artifact)
            .where(Artifact.user_id == user.id, Artifact.tombstoned_at.is_(None))
            .options(selectinload(Artifact.versions))
            .order_by(Artifact.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if artifact_type:
            stmt = stmt.where(Artifact.artifact_type == artifact_type)

        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def tombstone_artifact(self, artifact_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Soft-delete an artifact."""
        artifact = await self.get_artifact(artifact_id)
        if not artifact or artifact.tombstoned_at is not None:
            return False

        if artifact.user_id != user_id:
            raise PermissionError("User does not own this artifact")

        artifact.tombstoned_at = datetime.now(UTC)
        await self.session.flush()
        return True

    async def delete_user_artifacts(self, native_user_id: str) -> int:
        """Permanently delete all artifacts and their versions for a user."""
        user = await self.get_or_create_user(native_user_id)

        if self.storage is not None:
            art_stmt = select(Artifact.id).where(Artifact.user_id == user.id)
            art_ids = (await self.session.execute(art_stmt)).scalars().all()
            for art_id in art_ids:
                self.storage.delete_artifact_tree(user_id=user.id, artifact_id=art_id)

        del_stmt = delete(Artifact).where(Artifact.user_id == user.id)
        del_res = await self.session.execute(del_stmt)
        await self.session.flush()
        return int(getattr(del_res, "rowcount", 0))

    async def revert_to_version(
        self,
        artifact_id: uuid.UUID,
        target_version_num: int,
        user_id: uuid.UUID | None = None,
    ) -> tuple[Artifact, ArtifactVersion]:
        """Revert an artifact by appending a new version N+1 cloning target_version_num."""
        artifact = await self.get_artifact(artifact_id)
        if not artifact or artifact.tombstoned_at is not None:
            raise ValueError(f"Artifact {artifact_id} not found or tombstoned")

        if user_id is not None and artifact.user_id != user_id:
            raise PermissionError("User does not own this artifact")

        target_v = next((v for v in artifact.versions if v.version_num == target_version_num), None)
        if not target_v:
            raise ValueError(f"Version {target_version_num} not found")

        next_version_num = artifact.current_version_num + 1
        raw_data = target_v.binary_data
        content_sha256 = target_v.content_sha256 or hashlib.sha256(raw_data).hexdigest()
        file_size = target_v.file_size_bytes or len(raw_data)

        storage_path = None
        if self.storage is not None:
            storage_path, _, _ = self.storage.save(
                user_id=artifact.user_id,
                artifact_id=artifact_id,
                version_num=next_version_num,
                ext=artifact.artifact_type,
                data=raw_data,
            )

        now = datetime.now(UTC)
        version = ArtifactVersion(
            id=uuid.uuid4(),
            artifact_id=artifact_id,
            version_num=next_version_num,
            binary_data=raw_data,
            content_sha256=content_sha256,
            storage_path=storage_path,
            file_size_bytes=file_size,
            mime_type=target_v.mime_type
            or MIME_MAP.get(artifact.artifact_type, "application/octet-stream"),
            change_summary=f"Reverted to v{target_version_num}",
            created_at=now,
        )
        self.session.add(version)
        artifact.current_version_num = next_version_num
        artifact.updated_at = now
        await self.session.flush()
        await self.session.refresh(artifact, ["versions"])
        return artifact, version

    def _render_binary(
        self,
        artifact_type: str,
        workbook_spec: WorkbookSpec | None,
        document_spec: DocumentSpec | None,
        presentation_spec: PresentationSpec | None,
        raw_content: str | None,
        title: str,
        resume_spec: ResumeSpec | None = None,
    ) -> bytes:
        """Helper to render binary data according to spec and format."""
        if artifact_type == "xlsx":
            if workbook_spec:
                return XlsxGenerator.generate(workbook_spec)
            # Default empty sheet if spec omitted
            default_wb = WorkbookSpec(
                title=title,
                sheets=[
                    SheetSpec(name="Sheet1", headers=["Item", "Value"], rows=[["Sample", 100]])
                ],
            )
            return XlsxGenerator.generate(default_wb)

        elif artifact_type == "docx":
            if document_spec:
                return DocxGenerator.generate(document_spec)
            default_doc = DocumentSpec(
                title=title,
                sections=[
                    DocumentSectionSpec(
                        heading="Overview", paragraphs=[raw_content or "Initial content"]
                    )
                ],
            )
            return DocxGenerator.generate(default_doc)

        elif artifact_type == "pptx":
            if presentation_spec:
                return PptxGenerator.generate(presentation_spec)
            default_prs = PresentationSpec(
                title=title,
                slides=[SlideSpec(title="Overview", bullets=[raw_content or "Initial slide"])],
            )
            return PptxGenerator.generate(default_prs)

        elif artifact_type in ("markdown", "txt"):
            return (raw_content or f"# {title}\n\n").encode("utf-8")

        elif artifact_type == "pdf":
            if document_spec:
                return PdfGenerator.generate(document_spec)
            default_doc = DocumentSpec(
                title=title,
                sections=[
                    DocumentSectionSpec(
                        heading="Overview", paragraphs=[raw_content or "Initial content"]
                    )
                ],
            )
            return PdfGenerator.generate(default_doc)

        elif artifact_type == "typst":
            if raw_content:
                return TypstGenerator.compile_markup(raw_content)
            elif document_spec:
                return TypstGenerator.generate_report(document_spec)
            elif resume_spec:
                return TypstGenerator.generate_resume(resume_spec)
            else:
                default_markup = (
                    f'#set page(paper: "a4")\n= {title}\n\n'
                    f"{raw_content or 'Initial document content.'}"
                )
                return TypstGenerator.compile_markup(default_markup)

        elif artifact_type == "resume":
            if resume_spec:
                return TypstGenerator.generate_resume(resume_spec)
            default_resume = ResumeSpec(name=title, summary=raw_content or "Professional Resume")
            return TypstGenerator.generate_resume(default_resume)

        return (raw_content or "").encode("utf-8")


def extract_artifact_text(artifact_type: str, data: bytes) -> list[str]:
    """Extract plain text lines from binary artifact data for unified diffing."""
    if not data:
        return []

    if artifact_type == "docx":
        try:
            doc = Document(io.BytesIO(data))
            lines: list[str] = []
            for p in doc.paragraphs:
                if p.text:
                    lines.extend(p.text.splitlines())
            for tbl in doc.tables:
                for row in tbl.rows:
                    row_str = " | ".join(cell.text.strip() for cell in row.cells)
                    if row_str.strip():
                        lines.append(row_str)
            return lines
        except Exception:  # noqa: BLE001
            return data.decode("utf-8", errors="replace").splitlines()

    elif artifact_type == "xlsx":
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
            lines = []
            for sheet in wb.worksheets:
                lines.append(f"--- Sheet: {sheet.title} ---")
                for row in sheet.iter_rows(values_only=True):
                    if any(v is not None for v in row):
                        row_str = "\t".join("" if v is None else str(v) for v in row)
                        lines.append(row_str)
            return lines
        except Exception:  # noqa: BLE001
            return data.decode("utf-8", errors="replace").splitlines()

    elif artifact_type == "pptx":
        try:
            prs = Presentation(io.BytesIO(data))
            lines = []
            for idx, slide in enumerate(prs.slides):
                lines.append(f"--- Slide {idx + 1} ---")
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for p in shape.text_frame.paragraphs:
                            if p.text:
                                lines.extend(p.text.splitlines())
            return lines
        except Exception:  # noqa: BLE001
            return data.decode("utf-8", errors="replace").splitlines()

    return data.decode("utf-8", errors="replace").splitlines()


def compute_unified_diff(
    lines_v1: list[str],
    lines_v2: list[str],
    v1_num: int,
    v2_num: int,
) -> dict[str, Any]:
    """Compute unified diff lines, additions, and deletions between two versions."""
    diff_iter = difflib.unified_diff(
        lines_v1,
        lines_v2,
        fromfile=f"v{v1_num}",
        tofile=f"v{v2_num}",
        lineterm="",
    )
    diff_lines = list(diff_iter)
    additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    return {
        "v1": v1_num,
        "v2": v2_num,
        "diff_lines": diff_lines,
        "additions": additions,
        "deletions": deletions,
    }
