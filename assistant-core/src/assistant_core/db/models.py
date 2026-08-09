"""Import all assistant-core models for complete migration metadata."""

from assistant_core.events.models import EventInbox
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job

__all__ = ["EventInbox", "Job", "UserIdentity"]

