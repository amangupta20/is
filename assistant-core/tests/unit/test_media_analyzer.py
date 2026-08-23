"""Unit tests for Gemini-native MediaAnalyzer."""

import json

import httpx
import pytest

from assistant_core.media.analyzer import (
    MEDIA_ANALYSIS_FAILED_ERROR,
    MediaAnalysisError,
    MediaAnalyzer,
    canonical_media_url,
)


def _gemini_payload(analysis: dict) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(analysis)}]}}]}


def _analysis(url: str = "https://www.youtube.com/watch?v=dQw4w9WgXcQ") -> dict:
    return {
        "url": url,
        "media_type": "youtube",
        "title": "Rick Astley - Never Gonna Give You Up (Official Music Video)",
        "description": "The official video for Never Gonna Give You Up by Rick Astley.",
        "channel_or_author": "Rick Astley",
        "duration_seconds": 213,
        "summary": "The video features Rick Astley performing his hit single with backup dancers.",
        "key_takeaways": [
            "Pivotal 1980s dance-pop track produced by Stock Aitken Waterman",
            "Became a global internet phenomenon known as Rickrolling",
        ],
        "topics": ["music", "pop", "80s", "dance-pop"],
        "segments": [
            {
                "segment_index": 0,
                "start_time_seconds": 0,
                "end_time_seconds": 45,
                "label": "Intro and First Verse",
                "content": "Opening instrumental and Rick singing verse 1.",
            },
            {
                "segment_index": 1,
                "start_time_seconds": 45,
                "end_time_seconds": 120,
                "label": "Chorus and Dance Routine",
                "content": "Famous chorus dance moves and key vocal hooks.",
            },
        ],
    }


@pytest.mark.anyio
async def test_analyze_media_sends_native_file_part_and_parses() -> None:
    """The URL rides as file_data.file_uri, prompt follows it, JSON comes back."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "generativelanguage.googleapis.com"
        assert "/v1beta/models/gemini-2.5-flash:generateContent" in str(request.url)
        assert request.headers["x-goog-api-key"] == "test-key"
        body = json.loads(request.content.decode("utf-8"))
        parts = body["contents"][0]["parts"]
        assert parts[0] == {
            "file_data": {"file_uri": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        }
        assert parts[1]["text"].startswith("Watch this video in full and transcribe")
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(200, json=_gemini_payload(_analysis()))

    transport = httpx.MockTransport(handler)
    analyzer = MediaAnalyzer(api_key="test-key", model="gemini-2.5-flash", transport=transport)

    result = await analyzer.analyze_media("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert result.title == "Rick Astley - Never Gonna Give You Up (Official Music Video)"
    assert result.channel_or_author == "Rick Astley"
    assert result.duration_seconds == 213
    assert len(result.segments) == 2
    assert result.segments[1].end_time_seconds == 120


@pytest.mark.anyio
async def test_analyze_media_canonicalizes_short_links_for_request() -> None:
    """youtu.be links are normalized to watch URLs before the Gemini call."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        seen.append(body["contents"][0]["parts"][0]["file_data"]["file_uri"])
        analysis = _analysis()
        analysis["url"] = seen[-1]
        return httpx.Response(200, json=_gemini_payload(analysis))

    transport = httpx.MockTransport(handler)
    analyzer = MediaAnalyzer(api_key="test-key", transport=transport)

    result = await analyzer.analyze_youtube("https://youtu.be/dQw4w9WgXcQ?si=abc123")
    assert seen == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
    assert result.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.anyio
async def test_analyze_media_markdown_code_fence() -> None:
    json_data = _analysis("https://youtu.be/sample123")
    markdown_content = f"```json\n{json.dumps(json_data)}\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": markdown_content}]}}]},
        )

    transport = httpx.MockTransport(handler)
    analyzer = MediaAnalyzer(api_key="test-key", transport=transport)

    result = await analyzer.analyze_youtube("https://youtu.be/sample123")
    assert result.title.startswith("Rick Astley")


@pytest.mark.anyio
async def test_analyze_media_empty_url_raises_error() -> None:
    analyzer = MediaAnalyzer(api_key="test-key")
    with pytest.raises(MediaAnalysisError):
        await analyzer.analyze_media("   ")


@pytest.mark.anyio
async def test_analyze_media_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "invalid"}})

    transport = httpx.MockTransport(handler)
    analyzer = MediaAnalyzer(api_key="test-key", transport=transport)

    with pytest.raises(MediaAnalysisError) as exc_info:
        await analyzer.analyze_media("https://www.youtube.com/watch?v=private99")
    assert exc_info.value.code == MEDIA_ANALYSIS_FAILED_ERROR


@pytest.mark.anyio
async def test_analyze_media_malformed_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]},
        )

    transport = httpx.MockTransport(handler)
    analyzer = MediaAnalyzer(api_key="test-key", transport=transport)

    with pytest.raises(MediaAnalysisError):
        await analyzer.analyze_media("https://www.youtube.com/watch?v=badjson")


def test_canonical_media_url_normalizes_all_forms() -> None:
    canonical = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert canonical_media_url("https://youtu.be/dQw4w9WgXcQ") == canonical
    assert canonical_media_url("https://youtu.be/dQw4w9WgXcQ?si=kQZeRnr9") == canonical
    assert canonical_media_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=x") == canonical
    assert canonical_media_url("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=30s") == canonical
    assert canonical_media_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") == canonical
    assert canonical_media_url("https://example.com/video") is None
    assert canonical_media_url("") is None
