"""Import all assistant-core models for complete migration metadata."""

from assistant_core.artifacts.models import Artifact, ArtifactVersion, OnlyOfficeSession
from assistant_core.conversation.models import ConversationReference, ConversationSegment
from assistant_core.events.models import EventInbox
from assistant_core.files.models import FileDocument, FileReference, FileSegment
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.memory.models import (
    ChatProfileSnapshot,
    ConsolidationRun,
    MemoryEvidence,
    MemoryRecord,
)
from assistant_core.turns.models import CompletedTurn

__all__ = [
    "Artifact",
    "ArtifactVersion",
    "ChatProfileSnapshot",
    "CompletedTurn",
    "ConsolidationRun",
    "ConversationReference",
    "ConversationSegment",
    "EventInbox",
    "FileDocument",
    "FileReference",
    "FileSegment",
    "Job",
    "MemoryEvidence",
    "MemoryRecord",
    "OnlyOfficeSession",
    "UserIdentity",
]
