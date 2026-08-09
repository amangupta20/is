"""
title: Assistant Context
version: 0.1.0
requirements: httpx
"""

import hashlib
import hmac
import json
import time

import httpx
from pydantic import BaseModel, Field


class Filter:
    """Fail-open Open WebUI Filter for optional companion context."""

    class Valves(BaseModel):
        """Administrator-managed companion connection settings."""

        assistant_core_url: str = Field(default="http://assistant-core:8080")
        hmac_secret: str = Field(
            default="development-hmac-secret-change-me",
            json_schema_extra={"input": {"type": "password"}},
        )
        timeout_seconds: float = Field(default=1.5, ge=0.1, le=5.0)
        max_context_tokens: int = Field(default=4_000, ge=0, le=16_000)
        priority: int = Field(default=-100)

    def __init__(self) -> None:
        """Initialise the Filter with its administrator-configured valves."""
        self.valves = self.Valves()

    async def _post_context(self, payload: dict) -> dict:
        """Send one exactly signed request to the companion service."""
        path = "/v1/context"
        request_body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(time.time()))
        digest = hashlib.sha256(request_body).hexdigest()
        canonical = f"POST\n{path}\n{timestamp}\n{digest}".encode()
        secret = self.valves.hmac_secret.encode()
        signature = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(timeout=self.valves.timeout_seconds) as client:
            response = await client.post(
                f"{self.valves.assistant_core_url.rstrip('/')}{path}",
                content=request_body,
                headers={
                    "content-type": "application/json",
                    "x-assistant-timestamp": timestamp,
                    "x-assistant-signature": signature,
                },
            )
        response.raise_for_status()
        return response.json()

    async def inlet(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> dict:
        """Insert optional context immediately before the latest native user message."""
        messages = body.get("messages")
        if not isinstance(messages, list) or not __user__ or "id" not in __user__:
            return body

        latest_user_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if messages[index].get("role") == "user"),
            None,
        )
        if latest_user_index is None:
            return body

        metadata = __metadata__ or {}
        request_text = str(messages[latest_user_index].get("content", ""))[:16_000]
        try:
            response = await self._post_context(
                {
                    "native_user_id": str(__user__["id"]),
                    "native_chat_id": metadata.get("chat_id"),
                    "native_message_id": metadata.get("message_id"),
                    "request_text": request_text,
                    "max_tokens": self.valves.max_context_tokens,
                }
            )
            context_text = response.get("context_text") if isinstance(response, dict) else None
        except Exception:  # noqa: BLE001 - optional context must fail open for every companion failure.
            return body

        if not isinstance(context_text, str) or not context_text.strip():
            return body

        messages.insert(
            latest_user_index,
            {
                "role": "system",
                "content": f"<assistant_context>\n{context_text}\n</assistant_context>",
            },
        )
        return body
