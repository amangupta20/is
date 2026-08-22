"""Unit tests for Multimodal MediaAnalyzer."""

import json

import httpx
import pytest

from assistant_core.media.analyzer import (
    MEDIA_ANALYSIS_FAILED_ERROR,
    MediaAnalysisError,
    MediaAnalyzer,
)


@pytest.mark.anyio
async def test_analyze_media_success() -> None:
    """Verify clean multimodal media analysis from endpoint response."""
    response_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
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
                    )
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://test.litellm.local/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "gemini-2.0-flash"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=response_payload)

    transport = httpx.MockTransport(handler)
    analyzer = MediaAnalyzer(
        base_url="https://test.litellm.local/v1",
        api_key="test-key",
        model="gemini-2.0-flash",
        transport=transport,
    )

    result = await analyzer.analyze_media("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert result.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert result.title == "Rick Astley - Never Gonna Give You Up (Official Music Video)"
    assert result.channel_or_author == "Rick Astley"
    assert result.duration_seconds == 213
    assert len(result.key_takeaways) == 2
    assert len(result.topics) == 4
    assert len(result.segments) == 2
    assert result.segments[0].label == "Intro and First Verse"
    assert result.segments[1].end_time_seconds == 120


@pytest.mark.anyio
async def test_analyze_media_markdown_code_fence() -> None:
    """Verify robust parsing when response content is wrapped in markdown code blocks."""
    json_data = {
        "url": "https://youtu.be/sample123",
        "media_type": "youtube",
        "title": "System Architecture Overview",
        "description": "Deep dive into PostgreSQL pgvector and HNSW indexing",
        "channel_or_author": "Tech Talks",
        "duration_seconds": 600,
        "summary": "Explanation of vector search algorithms in PostgreSQL.",
        "key_takeaways": ["HNSW outperforms IVFFlat for high-dimensional recall"],
        "topics": ["database", "pgvector", "hnsw"],
        "segments": [
            {
                "segment_index": 0,
                "start_time_seconds": 0,
                "end_time_seconds": 300,
                "label": "Indexing Foundations",
                "content": "Graph construction for hierarchical navigable small worlds.",
            }
        ],
    }
    markdown_content = f"```json\n{json.dumps(json_data)}\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": markdown_content}}]},
        )

    transport = httpx.MockTransport(handler)
    analyzer = MediaAnalyzer(
        base_url="https://test.litellm.local/v1",
        api_key=None,
        model="gemini-2.0-flash",
        transport=transport,
    )

    result = await analyzer.analyze_youtube("https://youtu.be/sample123")
    assert result.title == "System Architecture Overview"
    assert len(result.segments) == 1
    assert result.segments[0].label == "Indexing Foundations"


@pytest.mark.anyio
async def test_analyze_media_empty_url_raises_error() -> None:
    """Verify empty or whitespace URL raises MediaAnalysisError immediately."""
    analyzer = MediaAnalyzer(
        base_url="https://test.litellm.local/v1",
        api_key=None,
        model="gemini-2.0-flash",
    )
    with pytest.raises(MediaAnalysisError) as exc_info:
        await analyzer.analyze_media("   ")
    assert MEDIA_ANALYSIS_FAILED_ERROR in str(exc_info.value)


@pytest.mark.anyio
async def test_analyze_media_http_error() -> None:
    """Verify HTTP error raises MediaAnalysisError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal server error"})

    transport = httpx.MockTransport(handler)
    analyzer = MediaAnalyzer(
        base_url="https://test.litellm.local/v1",
        api_key=None,
        model="gemini-2.0-flash",
        transport=transport,
    )

    with pytest.raises(MediaAnalysisError) as exc_info:
        await analyzer.analyze_media("https://www.youtube.com/watch?v=err123")
    assert MEDIA_ANALYSIS_FAILED_ERROR in str(exc_info.value)


@pytest.mark.anyio
async def test_analyze_media_malformed_json() -> None:
    """Verify invalid json response raises MediaAnalysisError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "This is not valid JSON at all"}}]},
        )

    transport = httpx.MockTransport(handler)
    analyzer = MediaAnalyzer(
        base_url="https://test.litellm.local/v1",
        api_key=None,
        model="gemini-2.0-flash",
        transport=transport,
    )

    with pytest.raises(MediaAnalysisError) as exc_info:
        await analyzer.analyze_media("https://www.youtube.com/watch?v=badjson")
    assert MEDIA_ANALYSIS_FAILED_ERROR in str(exc_info.value)
