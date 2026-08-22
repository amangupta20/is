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
    res = consolidator.consolidate([])
    assert res.supersessions == []
    assert res.validity_updates == []
    assert res.reclassifications == []

    res2 = consolidator.consolidate([{"id": uuid.uuid4(), "statement": "I use python"}])
    assert res2.supersessions == []


def test_consolidator_parses_multi_action_result() -> None:
    id_1 = uuid.uuid4()
    id_2 = uuid.uuid4()
    id_3 = uuid.uuid4()
    id_4 = uuid.uuid4()

    response_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "supersessions": [
                                {
                                    "superseded_id": str(id_1),
                                    "superseded_by_id": str(id_2),
                                    "reason": "User updated from Python 3.11 to Python 3.12",
                                }
                            ],
                            "validity_updates": [
                                {
                                    "memory_id": str(id_3),
                                    "action": "set_expiration",
                                    "expires_at": "2026-09-18T15:00:00Z",
                                    "temporal_tag": "job_application",
                                    "reason": "Active job application is an in-progress milestone",
                                }
                            ],
                            "reclassifications": [
                                {
                                    "memory_id": str(id_4),
                                    "new_category": "infrastructure",
                                    "reason": "Supabase backup migration belongs in infrastructure",
                                }
                            ],
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
        {
            "id": id_1,
            "key": "preferences.python",
            "category": "preference",
            "statement": "I use Python 3.11",
        },
        {
            "id": id_2,
            "key": "preferences.python",
            "category": "preference",
            "statement": "I use Python 3.12",
        },
        {
            "id": id_3,
            "key": "career.datazip",
            "category": "project",
            "statement": "Applied at Datazip",
        },
        {
            "id": id_4,
            "key": "infra.supabase",
            "category": "fact",
            "statement": "Migrated Supabase backups",
        },
    ]

    result = consolidator.consolidate(memories)
    assert len(result.supersessions) == 1
    assert result.supersessions[0].superseded_id == id_1
    assert result.supersessions[0].superseded_by_id == id_2

    assert len(result.validity_updates) == 1
    assert result.validity_updates[0].memory_id == id_3
    assert result.validity_updates[0].temporal_tag == "job_application"

    assert len(result.reclassifications) == 1
    assert result.reclassifications[0].memory_id == id_4
    assert result.reclassifications[0].new_category == "infrastructure"


def test_consolidator_handles_backward_compatible_legacy_decisions() -> None:
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
                                    "reason": "Superseded old version",
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
        {"id": id_1, "statement": "A"},
        {"id": id_2, "statement": "B"},
    ]

    result = consolidator.consolidate(memories)
    assert len(result.supersessions) == 1
    assert result.supersessions[0].superseded_id == id_1


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
