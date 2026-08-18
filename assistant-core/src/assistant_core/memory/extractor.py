import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from assistant_core.memory.schemas import ExplicitMemoryCandidate

MEMORY_EXTRACTION_FAILED_ERROR = "memory_extraction_failed"
EXTRACTION_RUBRIC = """Extract directly stated durable facts, preferences, instructions, projects, decisions, or ongoing life/work context from the captured user text. Return a JSON object with a candidates array only. Every candidate must contain the required fields: key (lowercase dotted identifier, e.g. career.datazip_application, infra.supabase_backup, profile.response_style), category (a concise lowercase domain slug, e.g. "fact", "preference", "decision", "project", "career", "infrastructure", "homelab", "tooling", "learning", "finance", "health"), statement, and evidence_quote (exact source quote). If the statement represents an in-progress milestone, temporary task, deadline, or time-bound situation, you may optionally include temporal_tag ("in_progress", "job_application", "deadline", "temporary_preference", "scheduled_event", "transient_task") and expires_at (ISO 8601 timestamp string). If a specific date, deadline, interview, or event date is mentioned in the statement (e.g. 'scheduled for August 19, 2026' or 'deadline is next Monday'), calculate expires_at directly to the end of that date or the day after in UTC (e.g. '2026-08-20T00:00:00Z'). For permanent personal facts and preferences, omit expires_at. Produce no candidate for transient requests, pasted logs, quoted third-party text, assistant claims, secrets, or uncertainty."""


class MemoryExtractionError(ValueError):
    """Raised with one safe code when task-model extraction cannot complete."""


@dataclass(frozen=True, slots=True)
class CompletedTurnData:
    """Copied completed-turn data that is safe to use after releasing a DB read."""

    id: uuid.UUID
    user_content: str
    occurred_at: datetime | None = None


class _ExtractionResponse(BaseModel):
    """Strict local validation of the model's JSON result."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidates: list[ExplicitMemoryCandidate]


class TaskModelMemoryExtractor:
    """Call a cheap OpenAI-compatible task model with a fixed memory rubric."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def extract(
        self,
        turn: CompletedTurnData,
        *,
        reference_time: datetime | None = None,
    ) -> list[ExplicitMemoryCandidate]:
        """Extract validated candidates without exposing provider diagnostics."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        ref_time = turn.occurred_at or reference_time or datetime.now(UTC)
        ref_time_str = ref_time.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        system_content = f"{EXTRACTION_RUBRIC}\n\nCurrent Reference UTC Time: {ref_time_str}"

        request_body: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": turn.user_content},
            ],
        }
        try:
            with httpx.Client(timeout=self._timeout_seconds, transport=self._transport) as client:
                response = client.post(self._url, headers=headers, json=request_body)
                response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            return _ExtractionResponse.model_validate_json(content).candidates
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise MemoryExtractionError(MEMORY_EXTRACTION_FAILED_ERROR) from None
