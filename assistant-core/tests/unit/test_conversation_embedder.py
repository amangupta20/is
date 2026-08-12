"""Network-free contracts for the one-input conversation embedder."""

import json
import math

import httpx
import pytest


def test_embedder_posts_one_input_and_returns_exact_finite_dimension() -> None:
    """One passage produces one OpenAI-compatible embedding request."""
    from assistant_core.conversation.embedder import OpenAICompatibleEmbedder

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer embedding-secret"
        body = json.loads(request.content)
        assert body == {
            "model": "gemini-embedding-2-preview",
            "input": "bounded passage",
            "dimensions": 3,
        }
        assert isinstance(body["input"], str)
        return httpx.Response(200, json={"data": [{"embedding": [0.1, -0.2, 0]}]})

    embedder = OpenAICompatibleEmbedder(
        base_url="https://embedding.example/v1",
        api_key="embedding-secret",
        model="gemini-embedding-2-preview",
        dimension=3,
        timeout_seconds=3,
        transport=httpx.MockTransport(respond),
    )

    vector = embedder.embed_one("bounded passage")

    assert vector == [0.1, -0.2, 0.0]
    assert all(math.isfinite(value) for value in vector)


@pytest.mark.parametrize(
    "response_json",
    [
        {"data": [{"embedding": [0.1, 0.2]}]},
        {"data": [{"embedding": [0.1, float("nan"), 0.3]}]},
        {"data": [{"embedding": [0.1, 0.2, 0.3]}, {"embedding": [0.4]}]},
    ],
)
def test_embedder_rejects_invalid_provider_output_with_one_safe_error(
    response_json: object,
) -> None:
    """Provider detail never escapes strict vector validation."""
    from assistant_core.conversation.embedder import (
        CONVERSATION_EMBEDDING_FAILED_ERROR,
        ConversationEmbeddingError,
        OpenAICompatibleEmbedder,
    )

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json)

    embedder = OpenAICompatibleEmbedder(
        base_url="https://embedding.example/v1",
        api_key=None,
        model="embedding-model",
        dimension=3,
        timeout_seconds=3,
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ConversationEmbeddingError) as raised:
        embedder.embed_one("private passage")

    assert str(raised.value) == CONVERSATION_EMBEDDING_FAILED_ERROR
    assert "private passage" not in str(raised.value)
