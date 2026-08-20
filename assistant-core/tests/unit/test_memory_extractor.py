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
        rubric = " ".join(body["messages"][0]["content"].split())
        assert "lowercase dotted identifier" in rubric
        assert "concise lowercase domain slug" in rubric
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
    preference_turn = CompletedTurnData(id=uuid.uuid4(), user_content="I prefer concise answers.")
    transient_turn = CompletedTurnData(id=uuid.uuid4(), user_content="What time is it?")

    candidates = extractor.extract(preference_turn)

    assert candidates[0].key == "profile.response_style"
    assert candidates[0].category == "preference"
    assert candidates[0].evidence_quote == "I prefer concise answers."
    assert extractor.extract(transient_turn) == []


def test_extractor_parses_dynamic_category_and_temporal_tag() -> None:
    from assistant_core.memory.extractor import (
        CompletedTurnData,
        TaskModelMemoryExtractor,
    )

    response = {
        "candidates": [
            {
                "key": "career.datazip_application",
                "category": "career",
                "statement": "The user applied for a position at Datazip.",
                "evidence_quote": "I applied for a job at Datazip today.",
                "temporal_tag": "job_application",
                "expires_at": "2026-09-18T15:00:00Z",
            }
        ]
    }

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(response)}}]},
        )

    extractor = TaskModelMemoryExtractor(
        base_url="https://task-model.example",
        api_key=None,
        model="cheap-extractor",
        timeout_seconds=3,
        transport=httpx.MockTransport(respond),
    )

    turn = CompletedTurnData(id=uuid.uuid4(), user_content="I applied for a job at Datazip today.")
    candidates = extractor.extract(turn)

    assert len(candidates) == 1
    assert candidates[0].category == "career"
    assert candidates[0].temporal_tag == "job_application"
    assert candidates[0].expires_at is not None


def test_extractor_includes_assistant_content_and_file_context() -> None:
    from assistant_core.memory.extractor import (
        CompletedTurnData,
        TaskModelMemoryExtractor,
    )

    captured_prompt = ""

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal captured_prompt
        body = json.loads(request.content)
        captured_prompt = body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "key": "infra.supabase_backup",
                                            "category": "infrastructure",
                                            "statement": "The user decided to migrate Supabase backups to Dokploy native backups.",
                                            "evidence_quote": "Let's switch to Dokploy native backups.",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    extractor = TaskModelMemoryExtractor(
        base_url="https://task-model.example",
        api_key=None,
        model="cheap-extractor",
        timeout_seconds=3,
        transport=httpx.MockTransport(respond),
    )

    turn = CompletedTurnData(
        id=uuid.uuid4(),
        user_content="How should we handle database backup?",
        assistant_content="I recommend Dokploy native backups for scheduled postgres dumps.",
        file_context="--- Document: infra_spec.md ---\nDatabase: PostgreSQL 16 on Hetzner",
    )

    candidates = extractor.extract(turn)

    assert len(candidates) == 1
    assert candidates[0].key == "infra.supabase_backup"
    assert "[User Prompt]:\nHow should we handle database backup?" in captured_prompt
    assert "[Assistant Response]:\nI recommend Dokploy native backups for scheduled postgres dumps." in captured_prompt
    assert "[Attached File Context / Excerpts]:\n--- Document: infra_spec.md ---" in captured_prompt

