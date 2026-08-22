"""Task-model extraction engine for topic episodes with adaptive multi-topic splitting."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from assistant_core.episodes.schemas import TopicEpisodeExtraction

EPISODE_EXTRACTION_FAILED_ERROR = "episode_extraction_failed"

EPISODE_EXTRACTION_RUBRIC = """You are a high-level conversation summarization and topic episode extraction engine.
Analyze the provided sequence of conversation turns between a user and assistant from a chat session.

Task:
Decompose this conversation session into one or more high-density structured Topic Episodes.
- If the conversation focused on a single topic or project task, produce 1 comprehensive episode.
- If the conversation shifted between distinct topics (e.g., first troubleshooting Docker networking, then discussing database schema design), adaptively split it into multiple distinct topic episodes covering their respective turn message ID spans.

For each episode, provide:
1. title: A concise, descriptive title (e.g., "Docker Compose & Supavisor Connection Debugging").
2. topic_category: A lowercase category slug (e.g., "architecture", "debugging", "configuration", "research", "planning", "tooling", "general").
3. summary: A dense, multi-sentence executive synthesis of what was discussed, problem explored, and resolution.
4. decisions_made: An array of key technical, architectural, or workflow decisions agreed upon.
5. open_loops: An array of unresolved questions, pending next steps, or future milestones explicitly noted.
6. key_entities: An array of mentioned technologies, libraries, services, filenames, or core concepts.
7. start_message_id: The native message ID (from the turn) where this topic begins.
8. end_message_id: The native message ID where this topic ends.

Output Format:
Return a JSON object with an "episodes" array containing the extracted episode objects only:
{
  "episodes": [
    {
      "title": "...",
      "topic_category": "...",
      "summary": "...",
      "decisions_made": ["..."],
      "open_loops": ["..."],
      "key_entities": ["..."],
      "start_message_id": "...",
      "end_message_id": "..."
    }
  ]
}
"""


class EpisodeExtractionError(ValueError):
    """Raised when task-model episode extraction fails or produces invalid output."""


@dataclass(frozen=True, slots=True)
class TurnSummaryInput:
    """Turn payload for episode compilation."""

    user_message_id: str
    assistant_message_id: str
    user_content: str
    assistant_content: str
    occurred_at: datetime | None = None


class _EpisodeExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    episodes: list[TopicEpisodeExtraction]


class TaskModelEpisodeExtractor:
    """Invokes OpenAI-compatible LLM endpoint to compile turns into Topic Episodes."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def extract_episodes(
        self,
        turns: list[TurnSummaryInput],
    ) -> list[TopicEpisodeExtraction]:
        """Extract structured topic episodes from an ordered list of turns."""
        if not turns:
            return []

        # Build transcript text with message ID anchors
        transcript_lines: list[str] = []
        for i, turn in enumerate(turns, 1):
            timestamp_str = f" [{turn.occurred_at.isoformat()}]" if turn.occurred_at else ""
            transcript_lines.append(
                f"--- Turn {i} (UserMsgID: {turn.user_message_id}, AsstMsgID: {turn.assistant_message_id}){timestamp_str} ---"
            )
            transcript_lines.append(f"User:\n{turn.user_content}\n")
            transcript_lines.append(f"Assistant:\n{turn.assistant_content}\n")

        full_transcript = "\n".join(transcript_lines)
        user_prompt = f"Transcript to analyze:\n\n{full_transcript}"

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": EPISODE_EXTRACTION_RUBRIC},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout_seconds
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise EpisodeExtractionError(EPISODE_EXTRACTION_FAILED_ERROR) from exc

        try:
            raw_content = data["choices"][0]["message"]["content"]
            parsed_json = self._parse_json_robust(raw_content)
            result = _EpisodeExtractionResponse.model_validate(parsed_json)
            # Fallback message IDs if model left them blank
            first_msg_id = turns[0].user_message_id
            last_msg_id = turns[-1].assistant_message_id
            validated_episodes: list[TopicEpisodeExtraction] = []
            for ep in result.episodes:
                start_id = ep.start_message_id or first_msg_id
                end_id = ep.end_message_id or last_msg_id
                validated_episodes.append(
                    TopicEpisodeExtraction(
                        title=ep.title,
                        topic_category=ep.topic_category or "general",
                        summary=ep.summary,
                        decisions_made=ep.decisions_made,
                        open_loops=ep.open_loops,
                        key_entities=ep.key_entities,
                        start_message_id=start_id,
                        end_message_id=end_id,
                    )
                )
            return validated_episodes
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise EpisodeExtractionError(EPISODE_EXTRACTION_FAILED_ERROR) from exc

    def _parse_json_robust(self, text: str) -> dict[str, Any]:
        """Strip markdown fences or trailing noise if present."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
            cleaned = cleaned.strip()
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        return {}
