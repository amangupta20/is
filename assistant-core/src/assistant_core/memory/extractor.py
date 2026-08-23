import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from assistant_core.memory.schemas import ExplicitMemoryCandidate

MEMORY_EXTRACTION_FAILED_ERROR = "memory_extraction_failed"
MAX_INFERRED_CANDIDATES_PER_TURN = 2
EXTRACTION_RUBRIC = """You are a high-accuracy personal memory extraction engine.
Analyze the completed conversation turn (user prompt, assistant response, and any attached file excerpts) to extract directly stated or agreed durable facts, user preferences, explicit instructions, active projects, technical decisions, or ongoing life/work context.

Return a JSON object with a candidates array only. Every candidate must contain the required fields:
- key: lowercase dotted identifier (e.g. career.infosys_recruitment, infra.supabase_backup, profile.response_style, project.fastapi_migration)
- category: a concise lowercase domain slug (e.g. "fact", "preference", "decision", "project", "career", "infrastructure", "homelab", "tooling", "learning", "finance", "health")
- statement: clear, concise, durable statement synthesizing what was established, confirmed, or updated in this turn
- evidence_quote: exact verbatim source quote supporting this memory from the user prompt, assistant response, or attached file context

Temporal & Expiration Guidelines:
- If the statement represents an in-progress milestone, temporary task, deadline, or time-bound situation, include temporal_tag ("in_progress", "job_application", "deadline", "temporary_preference", "scheduled_event", "transient_task") and expires_at (ISO 8601 timestamp string).
- If a specific date, deadline, interview, or event date is mentioned in the statement or dialogue (e.g. 'scheduled for August 19, 2026' or 'deadline is next Monday'), calculate expires_at directly to the end of that date or the day after in UTC relative to Current Reference UTC Time (e.g. '2026-08-20T00:00:00Z').
- For permanent personal facts, preferences, and lasting decisions, omit expires_at.

Negative Criteria / Filter Out:
- Produce no candidates for transient chit-chat, generic Q&A without personal context, raw pasted logs (unless establishing a permanent infrastructure fact), third-party quotes, assistant claims unsupported by user context, secrets/tokens, or speculative uncertainty.

Inferred Candidates (use sparingly):
- In addition to explicit candidates, you may emit at most 2 "inferred" candidates per turn for durable cross-turn patterns that are never directly stated: communication style preferences, working rhythms, tooling habits, or recurring priorities.
- Every inferred candidate must set "kind": "inferred", phrase the statement as a tentative observed pattern (e.g. "User appears to prefer terse, code-first answers over long prose"), and still include an evidence_quote from this exact turn.
- Never infer sensitive attributes (health, finances, relationships, beliefs). When in doubt, emit nothing; most turns should produce zero inferred candidates."""


class MemoryExtractionError(ValueError):
    """Raised with one safe code when task-model extraction cannot complete."""


def _cap_inferred_candidates(
    candidates: list[ExplicitMemoryCandidate],
) -> list[ExplicitMemoryCandidate]:
    """Keep explicit candidates in full and bound speculative inferred output."""
    kept: list[ExplicitMemoryCandidate] = []
    inferred_seen = 0
    for candidate in candidates:
        if candidate.kind == "explicit":
            kept.append(candidate)
            continue
        if inferred_seen >= MAX_INFERRED_CANDIDATES_PER_TURN:
            continue
        inferred_seen += 1
        kept.append(candidate)
    return kept


@dataclass(frozen=True, slots=True)
class CompletedTurnData:
    """Copied completed-turn data that is safe to use after releasing a DB read."""

    id: uuid.UUID
    user_content: str
    assistant_content: str = ""
    occurred_at: datetime | None = None
    file_context: str | None = None


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

        turn_prompt_parts = [f"[User Prompt]:\n{turn.user_content}"]
        if turn.assistant_content and turn.assistant_content.strip():
            turn_prompt_parts.append(f"[Assistant Response]:\n{turn.assistant_content.strip()}")
        if turn.file_context and turn.file_context.strip():
            turn_prompt_parts.append(
                f"[Attached File Context / Excerpts]:\n{turn.file_context.strip()}"
            )

        user_message_content = "\n\n".join(turn_prompt_parts)

        request_body: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message_content},
            ],
        }
        try:
            with httpx.Client(timeout=self._timeout_seconds, transport=self._transport) as client:
                response = client.post(self._url, headers=headers, json=request_body)
                response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            candidates = _ExtractionResponse.model_validate_json(content).candidates
            return _cap_inferred_candidates(candidates)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise MemoryExtractionError(MEMORY_EXTRACTION_FAILED_ERROR) from None
