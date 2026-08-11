"""OpenAI-compatible extraction of source-linked explicit memory candidates."""

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from assistant_core.memory.schemas import ExplicitMemoryCandidate

MEMORY_EXTRACTION_FAILED_ERROR = "memory_extraction_failed"
EXTRACTION_RUBRIC = """Extract only directly stated durable facts, preferences,
instructions, projects, or decisions from the captured user text. Return a JSON
object with a candidates array only. Every candidate must contain exactly the
fields key, category, statement, and evidence_quote. The key must be a lowercase
dotted identifier such as profile.response_style. The category must be exactly
one of "fact", "preference", "instruction", "project", or "decision". The
evidence_quote must be an exact source quote from the captured user text. Produce
no candidate for transient requests, pasted logs, quoted third-party text,
assistant claims, secrets, or uncertainty."""


class MemoryExtractionError(ValueError):
    """Raised with one safe code when task-model extraction cannot complete."""


@dataclass(frozen=True, slots=True)
class CompletedTurnData:
    """Copied completed-turn data that is safe to use after releasing a DB read."""

    id: uuid.UUID
    user_content: str


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

    def extract(self, turn: CompletedTurnData) -> list[ExplicitMemoryCandidate]:
        """Extract validated candidates without exposing provider diagnostics."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request_body: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": EXTRACTION_RUBRIC},
                {"role": "user", "content": turn.user_content},
            ],
        }
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.post(self._url, headers=headers, json=request_body)
                response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            return _ExtractionResponse.model_validate_json(content).candidates
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise MemoryExtractionError(MEMORY_EXTRACTION_FAILED_ERROR) from None
