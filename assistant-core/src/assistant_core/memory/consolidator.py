"""Task-model memory consolidation, taxonomy reorganization, and validity lifecycle resolution."""

import uuid
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONSOLIDATION_RUBRIC = """You are an intelligent personal memory consolidation and lifecycle management engine.
Given a list of active personal memories for a user, perform a comprehensive review across three areas:

1. Supersessions (Conflicting or Obsolete Facts):
   - Identify direct contradictions, superseded plans, or newer updates.
   - Example: "Planning to migrate Supabase backups" (older) is superseded by "Migrated Supabase backups to Dokploy native backup system" (newer).
   - The newer authoritative memory must supersede the older obsolete memory.

2. Validity Updates (Temporal Lifecycle & In-Flight Context):
   - Review memories describing in-progress milestones, job applications, temporary tasks, or short-term workarounds that were marked Permanent.
   - For in-flight processes (e.g. "User applied for a position at Company X", "Currently debugging Supabase container restart issue"), set a realistic expiration window (e.g. 30 to 60 days from creation) and assign a clear temporal_tag (e.g. "job_application", "in_progress", "active_task", "deadline").
   - If an expiring memory has already completed or is fully resolved, set action to "expire_now".

3. Reclassifications (Cohesive Domain Taxonomy):
   - Review categories to ensure memories are grouped into clean, descriptive domain slugs (e.g. "career", "infrastructure", "homelab", "preference", "fact", "project", "tooling", "learning", "finance", "health").
   - Example: Move job applications from "project" or "fact" to "career". Move Docker/Compose/server setups to "infrastructure" or "homelab".

Return a JSON object with three arrays:
- "supersessions": [{"superseded_id": UUID, "superseded_by_id": UUID, "reason": str}]
- "validity_updates": [{"memory_id": UUID, "action": "set_expiration" | "extend_expiration" | "expire_now" | "mark_permanent", "expires_at": ISO8601_string | null, "temporal_tag": str | null, "reason": str}]
- "reclassifications": [{"memory_id": UUID, "new_category": str, "reason": str}]

If no changes are needed in an area, return an empty array for that key."""


class MemorySupersessionDecision(BaseModel):
    """One resolved conflict where a newer memory supersedes an older one."""

    model_config = ConfigDict(extra="forbid")

    superseded_id: uuid.UUID = Field(description="UUID of the older, superseded record")
    superseded_by_id: uuid.UUID = Field(description="UUID of the newer, superseding record")
    reason: str = Field(min_length=1, max_length=500, description="Reason for the supersession")


class MemoryValidityDecision(BaseModel):
    """Lifecycle or validity adjustment for an ongoing, temporary, or completed memory."""

    model_config = ConfigDict(extra="forbid")

    memory_id: uuid.UUID = Field(description="UUID of the memory record to update")
    action: Literal["set_expiration", "extend_expiration", "expire_now", "mark_permanent"] = Field(
        description="Action to perform on the memory's validity"
    )
    expires_at: str | None = Field(
        default=None, description="ISO 8601 timestamp string if setting expiration"
    )
    temporal_tag: str | None = Field(
        default=None, max_length=50, description="Tag describing the timeframe"
    )
    reason: str = Field(min_length=1, max_length=500, description="Reason for the validity adjustment")


class MemoryReclassificationDecision(BaseModel):
    """Reorganizing a memory into a more cohesive, accurate domain category."""

    model_config = ConfigDict(extra="forbid")

    memory_id: uuid.UUID = Field(description="UUID of the memory record to reclassify")
    new_category: str = Field(
        min_length=2,
        max_length=50,
        pattern=r"^[a-z][a-z0-9_-]*$",
        description="New clean lowercase category slug (e.g. career, infrastructure, homelab, preference, fact, project)",
    )
    reason: str = Field(min_length=1, max_length=500, description="Reason for the category reclassification")


class ConsolidationResult(BaseModel):
    """Strict validation of the task model's multi-action consolidation response."""

    model_config = ConfigDict(extra="forbid")

    supersessions: list[MemorySupersessionDecision] = Field(default_factory=list)
    validity_updates: list[MemoryValidityDecision] = Field(default_factory=list)
    reclassifications: list[MemoryReclassificationDecision] = Field(default_factory=list)


# Backward compatibility alias
MemoryConsolidationDecision = MemorySupersessionDecision


class MemoryConsolidationError(ValueError):
    """Raised when task-model consolidation cannot complete."""


class TaskModelMemoryConsolidator:
    """Call a task model to identify and resolve contradictory memory records, adjust validity, and reclassify categories."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def consolidate(
        self,
        memories: list[dict[str, Any]],
    ) -> ConsolidationResult:
        """Submit active memories to the task model and parse supersessions, validity updates, and reclassifications."""
        if len(memories) <= 1:
            return ConsolidationResult()

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        formatted_memories = [
            {
                "id": str(m["id"]),
                "key": m.get("key", ""),
                "category": m.get("category", ""),
                "statement": m.get("statement", ""),
                "temporal_tag": m.get("temporal_tag") or "permanent",
                "expires_at": str(m.get("expires_at") or "none"),
                "created_at": str(m.get("created_at", "")),
            }
            for m in memories
        ]

        request_body: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": CONSOLIDATION_RUBRIC},
                {
                    "role": "user",
                    "content": f"Active user memories for consolidation review:\n{formatted_memories}",
                },
            ],
        }

        try:
            with httpx.Client(timeout=self._timeout_seconds, transport=self._transport) as client:
                response = client.post(self._url, headers=headers, json=request_body)
                response.raise_for_status()

            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Content is not string")

            raw = content.strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            import json
            parsed_dict = json.loads(raw)
            if not isinstance(parsed_dict, dict):
                raise TypeError("Parsed JSON is not an object")

            # Handle backward compatibility if model returns legacy {"decisions": [...]}
            if "decisions" in parsed_dict and "supersessions" not in parsed_dict:
                parsed_dict["supersessions"] = parsed_dict.pop("decisions")
            if "validity_updates" not in parsed_dict:
                parsed_dict["validity_updates"] = []
            if "reclassifications" not in parsed_dict:
                parsed_dict["reclassifications"] = []

            return ConsolidationResult.model_validate(parsed_dict)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise MemoryConsolidationError(f"Memory consolidation failed: {exc}") from None
