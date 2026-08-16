"""Import all assistant-core models for complete migration metadata."""

from assistant_core.conversation.models import ConversationReference, ConversationSegment
from assistant_core.events.models import EventInbox
from assistant_core.files.models import FileReference, FileSegment
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.memory.models import ChatProfileSnapshot, MemoryEvidence, MemoryRecord
from assistant_core.turns.models import CompletedTurn

__all__ = [
    "ChatProfileSnapshot",
    "CompletedTurn",
    "ConversationReference",
    "ConversationSegment",
    "EventInbox",
    "FileReference",
    "FileSegment",
    "Job",
    "MemoryEvidence",
    "MemoryRecord",
    "UserIdentity",
]
