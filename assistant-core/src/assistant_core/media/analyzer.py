"""Multimodal media analysis engine utilizing Gemini / LiteLLM endpoints."""

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from assistant_core.media.schemas import MediaAnalysisResult

MEDIA_ANALYSIS_FAILED_ERROR = "media_analysis_failed"

MEDIA_ANALYSIS_RUBRIC = """You are an expert multimodal media understanding engine.
Analyze the provided video or media URL and provide a comprehensive structured analysis.

Task:
Extract detailed metadata, executive summary, key takeaways, topics, and timestamped segments/chapters.

Required Output Fields:
1. url: The URL of the media being analyzed.
2. media_type: "youtube" (or relevant media format).
3. title: A concise, descriptive title of the video or media.
4. description: A brief summary or description of the media.
5. channel_or_author: Name of the creator, presenter, or YouTube channel.
6. duration_seconds: Total duration in seconds (integer) if known or estimated, otherwise null.
7. summary: A dense, multi-paragraph executive synthesis of the key points, concepts, explanations, and demonstrations in the video.
8. key_takeaways: Array of distinct, actionable takeaways, conclusions, or facts established.
9. topics: Array of relevant subject categories, technologies, or keywords.
10. segments: Array of chronological timestamped segments/chapters:
    - segment_index: 0-indexed integer (0, 1, 2, ...)
    - start_time_seconds: Start timestamp in seconds (integer)
    - end_time_seconds: End timestamp in seconds (integer)
    - label: Concise chapter title or section topic
    - content: Rich textual summary of what is discussed, demonstrated, or taught during this timestamp span.

Output Format:
Return a valid JSON object matching the required fields only.
"""


class MediaAnalysisError(ValueError):
    """Raised when task-model media analysis fails or produces invalid output."""


class MediaAnalyzer:
    """Invokes multimodal Gemini / LiteLLM endpoints to analyze video and media resources."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def analyze_media(
        self,
        url: str,
        media_type: str = "youtube",
    ) -> MediaAnalysisResult:
        """Analyze a media URL (e.g. YouTube video) and return structured understanding."""
        if not url or not url.strip():
            raise MediaAnalysisError(MEDIA_ANALYSIS_FAILED_ERROR)

        user_prompt = f"Please analyze this {media_type} video in detail:\nURL: {url.strip()}"

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": MEDIA_ANALYSIS_RUBRIC},
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
            raise MediaAnalysisError(MEDIA_ANALYSIS_FAILED_ERROR) from exc

        try:
            raw_content = data["choices"][0]["message"]["content"]
            parsed_json = self._parse_json_robust(raw_content)
            # Ensure url and media_type fallback if omitted by LLM
            if isinstance(parsed_json, dict):
                if not parsed_json.get("url"):
                    parsed_json["url"] = url
                if not parsed_json.get("media_type"):
                    parsed_json["media_type"] = media_type
            result = MediaAnalysisResult.model_validate(parsed_json)
            # Fix up segment indices if necessary
            for idx, seg in enumerate(result.segments):
                if seg.segment_index is None or seg.segment_index == 0:
                    seg.segment_index = idx
            return result
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise MediaAnalysisError(MEDIA_ANALYSIS_FAILED_ERROR) from exc

    async def analyze_youtube(self, url: str) -> MediaAnalysisResult:
        """Convenience wrapper for YouTube URLs."""
        return await self.analyze_media(url=url, media_type="youtube")

    @staticmethod
    def _parse_json_robust(content: str) -> dict[str, Any]:
        """Robustly parse JSON string from model response, stripping markdown blocks if present."""
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

        # Match the first outermost JSON object
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        return {}
