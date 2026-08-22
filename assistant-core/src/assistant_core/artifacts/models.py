"""SQLAlchemy models for durable artifacts and OnlyOffice sessions."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assistant_core.db.base import Base


class Artifact(Base):
    """Represents a durable, versioned document or spreadsheet artifact."""

    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_user_tombstone", "user_id", "tombstoned_at"),
        {"schema": "assistant_core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    native_project_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    native_folder_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    current_version_num: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        onupdate=func.now(),
        nullable=False,
    )
    tombstoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list["ArtifactVersion"]] = relationship(
        "ArtifactVersion",
        back_populates="artifact",
        cascade="all, delete-orphan",
        order_by="ArtifactVersion.version_num",
    )


class ArtifactVersion(Base):
    """One immutable binary version in an artifact's revision chain."""

    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version_num", name="uq_artifact_version_num"),
        Index("ix_artifact_versions_artifact_id", "artifact_id"),
        {"schema": "assistant_core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.artifacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    binary_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_turn_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.completed_turn.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    artifact: Mapped["Artifact"] = relationship("Artifact", back_populates="versions")


class OnlyOfficeSession(Base):
    """Tracks an active or completed OnlyOffice editing session."""

    __tablename__ = "onlyoffice_sessions"
    __table_args__ = (
        Index("ix_onlyoffice_sessions_session_key", "session_key"),
        {"schema": "assistant_core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.artifacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
