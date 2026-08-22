"""Unit tests for TaskModelEpisodeExtractor."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from assistant_core.episodes.extractor import (
    EPISODE_EXTRACTION_FAILED_ERROR,
    EpisodeExtractionError,
    TaskModelEpisodeExtractor,
    TurnSummaryInput,
)


@pytest.mark.anyio
async def test_extract_episodes_success() -> None:
    """Verify clean extraction of multiple topic episodes from turn history."""
    response_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "episodes": [
                                {
                                    "title": "Docker Compose Networking Configuration",
                                    "topic_category": "infrastructure",
                                    "summary": "Discussed Supavisor pooler networking setup in Dokploy. Configured shared network open-webui-integrations.",
                                    "decisions_made": [
                                        "Attach Supavisor to open-webui-integrations network"
                                    ],
                                    "open_loops": [
                                        "Verify DNS resolution from open-webui container"
                                    ],
                                    "key_entities": ["Dokploy", "Supavisor", "Docker"],
                                    "start_message_id": "msg-1",
                                    "end_message_id": "msg-2",
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://test.litellm.local/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert len(payload["messages"]) == 2
        return httpx.Response(200, json=response_payload)

    transport = httpx.MockTransport(handler)
    extractor = TaskModelEpisodeExtractor(
        base_url="https://test.litellm.local/v1",
        api_key="test-key",
        model="test-model",
        transport=transport,
    )

    turns = [
        TurnSummaryInput(
            user_message_id="msg-1",
            assistant_message_id="msg-2",
            user_content="How do we connect Supavisor across networks?",
            assistant_content="Attach it to open-webui-integrations.",
            occurred_at=datetime.now(UTC),
        )
    ]

    episodes = await extractor.extract_episodes(turns)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.title == "Docker Compose Networking Configuration"
    assert ep.topic_category == "infrastructure"
    assert len(ep.decisions_made) == 1
    assert len(ep.open_loops) == 1
    assert len(ep.key_entities) == 3
    assert ep.start_message_id == "msg-1"
    assert ep.end_message_id == "msg-2"


@pytest.mark.anyio
async def test_extract_episodes_error_handling() -> None:
    """Verify proper exception raising on HTTP failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal error"})

    transport = httpx.MockTransport(handler)
    extractor = TaskModelEpisodeExtractor(
        base_url="https://test.litellm.local/v1",
        api_key="test-key",
        model="test-model",
        transport=transport,
    )

    turns = [
        TurnSummaryInput(
            user_message_id="msg-1",
            assistant_message_id="msg-2",
            user_content="Hello",
            assistant_content="Hi",
        )
    ]

    with pytest.raises(EpisodeExtractionError) as exc_info:
        await extractor.extract_episodes(turns)
    assert EPISODE_EXTRACTION_FAILED_ERROR in str(exc_info.value)
