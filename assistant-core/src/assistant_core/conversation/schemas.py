"""Immutable internal values for bounded conversation passages."""

import uuid
from dataclasses import dataclass
from typing import Literal

ConversationRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class PassageDraft:
    """One exact bounded substring of a native conversation message."""

    role: ConversationRole
    role_order: int
    native_message_id: str
    chunk_ordinal: int
    content: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class PassageMaterialization:
    """Content-free counts and IDs produced by lexical materialization."""

    new_segments: int
    reused_segments: int
    new_references: int
    missing_embedding_ids: tuple[uuid.UUID, ...]
