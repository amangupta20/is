"""Task-model memory consolidation and conflict resolution."""

import uuid
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONSOLIDATION_RUBRIC = """You are an explicit memory consolidation system.
Given a list of active personal memories for a user, identify direct contradictions, updates, or supersessions.
For example:
- "User works with Python 3.11" (older) is superseded by "User updated all projects to Python 3.12" (newer).
- "User lives in New York" (older) is superseded by "User moved to London" (newer).
- "User prefers light theme" (older) is superseded by "User prefers dark theme" (newer).

Rules:
1. ONLY supersede a memory if there is an unambiguous conflict, direct contradiction, or explicit update.
2. If two memories are complementary or describe different aspects, DO NOT supersede them.
3. The newer statement must supersede the older statement.
4. Return a JSON object with a "decisions" array.
5. Each decision must contain:
   - "superseded_id": UUID of the older, now obsolete memory record
   - "superseded_by_id": UUID of the newer, authoritative memory record
   - "reason": A brief explanation of the contradiction or update
6. If no conflicts or updates exist, return an empty decisions array."""


class MemoryConsolidationDecision(BaseModel):
    """One resolved conflict where a newer memory supersedes an older one."""

    model_config = ConfigDict(extra="forbid", strict=True)

    superseded_id: uuid.UUID = Field(description="UUID of the older, superseded record")
    superseded_by_id: uuid.UUID = Field(description="UUID of the newer, superseding record")
    reason: str = Field(min_length=1, max_length=500, description="Reason for the supersession")


class _ConsolidationResponse(BaseModel):
    """Strict validation of the task model's JSON consolidation response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    decisions: list[MemoryConsolidationDecision]


class MemoryConsolidationError(ValueError):
    """Raised when task-model consolidation cannot complete."""


class TaskModelMemoryConsolidator:
    """Call a task model to identify and resolve contradictory memory records."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 15.0,
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
    ) -> list[MemoryConsolidationDecision]:
        """Submit active memories to the task model and parse supersession decisions."""
        if len(memories) <= 1:
            return []

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        formatted_memories = [
            {
                "id": str(m["id"]),
                "key": m.get("key", ""),
                "category": m.get("category", ""),
                "statement": m.get("statement", ""),
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
                    "content": f"Active user memories for consolidation:\n{formatted_memories}",
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

            return _ConsolidationResponse.model_validate_json(content).decisions
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise MemoryConsolidationError(f"Memory consolidation failed: {exc}") from None
