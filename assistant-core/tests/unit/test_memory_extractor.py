"""Focused contract for the cheap-model explicit-memory extractor."""

import json
import uuid

import httpx


def test_extractor_parses_explicit_preference_and_empty_candidates() -> None:
    """The provider contract accepts one preference and an intentionally empty result."""
    from assistant_core.memory.extractor import (
        CompletedTurnData,
        TaskModelMemoryExtractor,
    )

    responses = iter(
        [
            {
                "candidates": [
                    {
                        "key": "profile.response_style",
                        "category": "preference",
                        "statement": "The user prefers concise answers.",
                        "evidence_quote": "I prefer concise answers.",
                    }
                ]
            },
            {"candidates": []},
        ]
    )

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "cheap-extractor"
        assert body["temperature"] == 0
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(next(responses))}}]},
        )

    extractor = TaskModelMemoryExtractor(
        base_url="https://task-model.example",
        api_key=None,
        model="cheap-extractor",
        timeout_seconds=3,
        transport=httpx.MockTransport(respond),
    )
    preference_turn = CompletedTurnData(
        id=uuid.uuid4(), user_content="I prefer concise answers."
    )
    transient_turn = CompletedTurnData(id=uuid.uuid4(), user_content="What time is it?")

    candidates = extractor.extract(preference_turn)

    assert candidates[0].key == "profile.response_style"
    assert candidates[0].evidence_quote == "I prefer concise answers."
    assert extractor.extract(transient_turn) == []
