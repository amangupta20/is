"""Strict one-input OpenAI-compatible conversation embedding client."""

import math
from typing import Any

import httpx

CONVERSATION_EMBEDDING_FAILED_ERROR = "conversation_embedding_failed"
EMBEDDING_VERSION = "openai-compatible-v1"


class ConversationEmbeddingError(RuntimeError):
    """Raised with one content-free code when embedding cannot be completed."""


class OpenAICompatibleEmbedder:
    """Embed exactly one bounded passage per HTTP request."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        dimension: int,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._api_key = api_key
        self.model = model
        self.dimension = dimension
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def embed_one(self, text: str) -> list[float]:
        """Return one finite vector or a single redacted failure."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request_body: dict[str, Any] = {
            "model": self.model,
            "input": text,
            "dimensions": self.dimension,
        }
        try:
            with httpx.Client(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = client.post(self._url, headers=headers, json=request_body)
                response.raise_for_status()
            payload = response.json()
            data = payload["data"]
            if not isinstance(data, list) or len(data) != 1:
                raise TypeError
            embedding = data[0]["embedding"]
            if not isinstance(embedding, list) or len(embedding) != self.dimension:
                raise TypeError
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in embedding
            ):
                raise TypeError
            return [float(value) for value in embedding]
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise ConversationEmbeddingError(
                CONVERSATION_EMBEDDING_FAILED_ERROR
            ) from None
