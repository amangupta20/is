import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assistant_core.db.base import Base


class MemoryRecord(Base):
    """A durable explicit memory, retained across supersession."""

    __tablename__ = "memory_record"
    __table_args__ = (
        CheckConstraint(
            "char_length(category) >= 2 AND char_length(category) <= 50",
            name="memory_record_category_len",
        ),
        CheckConstraint("kind = 'explicit'", name="memory_record_kind"),
        CheckConstraint("confidence = 1", name="memory_record_confidence"),
        CheckConstraint(
            "char_length(statement) <= 2000",
            name="memory_record_statement_length",
        ),
        CheckConstraint(
            "state IN ('active', 'superseded', 'archived', 'expired')",
            name="memory_record_state",
        ),
        Index(
            "uq_memory_record_active_user_key",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_memory_record_user_id_key", "user_id", "key"),
        Index("ix_memory_record_user_validity", "user_id", "state", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.user_identity.id"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(30), nullable=False, default="explicit", server_default="explicit"
    )
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    temporal_tag: Mapped[str | None] = mapped_column(String(50), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.memory_record.id"), nullable=True
    )


class MemoryEvidence(Base):
    """Exact user-message provenance for one explicit memory record."""

    __tablename__ = "memory_evidence"
    __table_args__ = (
        CheckConstraint(
            "char_length(evidence_quote) <= 1000",
            name="memory_evidence_evidence_quote_length",
        ),
        Index(
            "uq_memory_evidence_record_turn_quote",
            "memory_record_id",
            "completed_turn_id",
            "evidence_quote",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    memory_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.memory_record.id"), nullable=False
    )
    completed_turn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.completed_turn.id"), nullable=False
    )
    native_user_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChatProfileSnapshot(Base):
    """Exact profile rendering for one native chat and its ordered memory sources."""

    __tablename__ = "chat_profile_snapshot"
    __table_args__ = (
        Index("uq_chat_profile_snapshot_user_chat", "user_id", "native_chat_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.user_identity.id"), nullable=False
    )
    native_chat_id: Mapped[str] = mapped_column(String(200), nullable=False)
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_memory_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConsolidationRun(Base):
    """Historical execution log and diff ledger for memory consolidation runs."""

    __tablename__ = "consolidation_run"
    __table_args__ = (
        Index(
            "ix_consolidation_run_user_time",
            "native_user_id",
            "created_at",
            postgresql_using="btree",
        ),
        Index(
            "ix_consolidation_run_created_at",
            "created_at",
            postgresql_using="btree",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.user_identity.id"), nullable=True
    )
    native_user_id: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger: Mapped[str] = mapped_column(String(50), nullable=False, default="manual_admin")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="success")
    memories_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    superseded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MemoryChangeLog(Base):
    """Historical audit ledger capturing every memory creation, update, and deletion across all sources."""

    __tablename__ = "memory_change_log"
    __table_args__ = (
        Index(
            "ix_memory_change_log_user_time",
            "native_user_id",
            "created_at",
            postgresql_using="btree",
        ),
        Index(
            "ix_memory_change_log_memory_id",
            "memory_id",
            postgresql_using="btree",
        ),
        Index(
            "ix_memory_change_log_created_at",
            "created_at",
            postgresql_using="btree",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.user_identity.id"), nullable=True
    )
    native_user_id: Mapped[str] = mapped_column(String(200), nullable=False)
    memory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.memory_record.id"), nullable=True
    )
    change_source: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    previous_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_reverted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


