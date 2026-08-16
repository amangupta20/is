"""Unit tests for task-model memory consolidator."""

import json
import uuid

import httpx
import pytest

from assistant_core.memory.consolidator import (
    MemoryConsolidationError,
    TaskModelMemoryConsolidator,
)


def test_consolidator_returns_empty_when_few_memories() -> None:
    consolidator = TaskModelMemoryConsolidator(
        base_url="http://mock-llm.local",
        api_key="secret",
        model="gpt-4o-mini",
    )
    # 0 or 1 memory requires no LLM call
    assert consolidator.consolidate([]) == []
    assert consolidator.consolidate([{"id": uuid.uuid4(), "statement": "I use python"}]) == []


def test_consolidator_parses_successful_decisions() -> None:
    id_1 = uuid.uuid4()
    id_2 = uuid.uuid4()

    response_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "decisions": [
                                {
                                    "superseded_id": str(id_1),
                                    "superseded_by_id": str(id_2),
                                    "reason": "User updated from Python 3.11 to Python 3.12",
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response_payload))

    consolidator = TaskModelMemoryConsolidator(
        base_url="http://mock-llm.local",
        api_key="secret",
        model="gpt-4o-mini",
        transport=transport,
    )

    memories = [
        {"id": id_1, "key": "preferences.python", "statement": "I use Python 3.11"},
        {"id": id_2, "key": "preferences.python", "statement": "I use Python 3.12"},
    ]

    decisions = consolidator.consolidate(memories)
    assert len(decisions) == 1
    assert decisions[0].superseded_id == id_1
    assert decisions[0].superseded_by_id == id_2
    assert "Python 3.12" in decisions[0].reason


def test_consolidator_handles_empty_decisions() -> None:
    response_payload = {"choices": [{"message": {"content": json.dumps({"decisions": []})}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response_payload))

    consolidator = TaskModelMemoryConsolidator(
        base_url="http://mock-llm.local",
        api_key="secret",
        model="gpt-4o-mini",
        transport=transport,
    )

    memories = [
        {"id": uuid.uuid4(), "statement": "I like dark mode"},
        {"id": uuid.uuid4(), "statement": "I work in engineering"},
    ]

    decisions = consolidator.consolidate(memories)
    assert decisions == []


def test_consolidator_raises_on_http_or_format_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="Internal Error"))
    consolidator = TaskModelMemoryConsolidator(
        base_url="http://mock-llm.local",
        api_key="secret",
        model="gpt-4o-mini",
        transport=transport,
    )

    memories = [
        {"id": uuid.uuid4(), "statement": "A"},
        {"id": uuid.uuid4(), "statement": "B"},
    ]

    with pytest.raises(MemoryConsolidationError):
        consolidator.consolidate(memories)
