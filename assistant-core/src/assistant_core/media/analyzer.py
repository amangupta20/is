"""Gemini-native multimodal media analysis via YouTube URL file parts."""

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from assistant_core.media.schemas import MediaAnalysisResult

MEDIA_ANALYSIS_FAILED_ERROR = "media_analysis_failed"
GEMINI_GENERATE_CONTENT_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

ANALYSIS_PROMPT = """Watch this video in full and transcribe it completely.

Return ONLY a JSON object with exactly these fields:
- title: the video's actual title (title card or spoken intro)
- description: one factual sentence describing the video
- channel_or_author: the actual channel name or speaker
- duration_seconds: integer total duration in seconds
- summary: one factual sentence (the full content lives in segments)
- key_takeaways: leave as an empty array; do not editorialize
- topics: array of subject keywords covered
- segments: chronological spans covering 100% of the runtime in roughly 30-60 second chunks:
  - segment_index: 0-indexed integer
  - start_time_seconds: integer start in seconds (use observed MM:SS timestamps)
  - end_time_seconds: integer end in seconds
  - label: short chapter-style label for the span
  - content: the near-verbatim spoken transcript of this span, preserving actual
    wording, names, and terms; when something notable happens visually, append a
    separate line starting with "Visual: " (demos, on-screen text, scene changes)

Rules:
- Transcribe EVERYTHING said from start to finish; never summarize within a span.
- Spans must be contiguous and cover the entire runtime with no gaps.
- Never invent content; use exact names and terms as spoken or shown."""


class MediaAnalysisError(ValueError):
    """Raised when Gemini media analysis fails or produces invalid output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_media_url(url: str) -> str | None:
    """Normalize any YouTube URL form to one canonical watch URL for dedup."""
    text = (url or "").strip()
    if not text:
        return None
    patterns = (
        r"^https?://(?:www\.|m\.)?youtube\.com/watch\?(?:[^#]*&)?v=([\w-]{6,20})",
        r"^https?://(?:www\.)?youtu\.be/([\w-]{6,20})",
        r"^https?://(?:www\.|m\.)?youtube\.com/shorts/([\w-]{6,20})",
        r"^https?://(?:www\.)?youtube\.com/embed/([\w-]{6,20})",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return f"https://www.youtube.com/watch?v={match.group(1)}"
    return None


class MediaAnalyzer:
    """Analyze public YouTube videos through Gemini's native video understanding."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-2.5-flash",
        timeout_seconds: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def analyze_youtube(self, url: str) -> MediaAnalysisResult:
        return await self.analyze_media(url=url, media_type="youtube")

    async def analyze_media(
        self,
        url: str,
        media_type: str = "youtube",
    ) -> MediaAnalysisResult:
        """Send one YouTube URL as a native video part and parse structured output."""
        canonical = canonical_media_url(url) if media_type == "youtube" else None
        target = canonical or (url or "").strip()
        if not target:
            raise MediaAnalysisError(MEDIA_ANALYSIS_FAILED_ERROR)

        payload: dict[str, Any] = {
            "contents": [
                {
                    "parts": [
                        {"file_data": {"file_uri": target}},
                        {"text": ANALYSIS_PROMPT},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout_seconds
            ) as client:
                response = await client.post(
                    GEMINI_GENERATE_CONTENT_URL.format(model=self._model),
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": self._api_key,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise MediaAnalysisError(MEDIA_ANALYSIS_FAILED_ERROR) from exc

        try:
            candidates = data["candidates"]
            parts = candidates[0]["content"]["parts"]
            raw_text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
            parsed = self._parse_json_robust(raw_text)
            if not isinstance(parsed, dict):
                raise TypeError("analysis payload was not a JSON object")
            if not parsed.get("url"):
                parsed["url"] = target
            if not parsed.get("media_type"):
                parsed["media_type"] = media_type
            result = MediaAnalysisResult.model_validate(parsed)
            for idx, segment in enumerate(result.segments):
                if segment.segment_index is None or segment.segment_index == 0:
                    segment.segment_index = idx
            return result
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise MediaAnalysisError(MEDIA_ANALYSIS_FAILED_ERROR) from exc

    @staticmethod
    def _parse_json_robust(content: str) -> dict[str, Any]:
        """Parse the model's JSON text, tolerating markdown fences."""
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\n?```$", "", text)
            text = text.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        return {}
