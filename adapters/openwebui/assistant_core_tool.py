"""
title: Assistant Core Status
version: 0.1.0
requirements: httpx
"""

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, Field


class Tools:
    """Explicit tools for the optional Assistant Core companion."""

    _UNAVAILABLE = (
        "Assistant Core is unavailable. Ordinary chat can continue without custom context."
    )

    class Valves(BaseModel):
        """Administrator-managed companion connection settings."""

        assistant_core_url: str = Field(default="http://assistant-core:8080")
        hmac_secret: str = Field(
            default="development-hmac-secret-change-me",
            json_schema_extra={"input": {"type": "password"}},
        )
        timeout_seconds: float = Field(default=3.0, ge=0.1, le=10.0)

    def __init__(self) -> None:
        """Initialise the Tool with administrator-configured valves."""
        self.valves = self.Valves()

    @staticmethod
    def _optional_id(container: object, key: str) -> str | None:
        if not isinstance(container, Mapping):
            return None
        identifier = container.get(key)
        return None if identifier is None else str(identifier)

    async def assistant_status(
        self,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Check whether Assistant Core is available and show aggregate queue counts."""
        native_user_id = self._optional_id(__user__, "id") or "unknown"
        payload = {
            "native_user_id": native_user_id,
            "native_chat_id": self._optional_id(__metadata__, "chat_id"),
            "native_message_id": self._optional_id(__metadata__, "message_id"),
        }

        try:
            path = "/v1/status"
            request_body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            timestamp = str(int(time.time()))
            digest = hashlib.sha256(request_body).hexdigest()
            canonical = f"POST\n{path}\n{timestamp}\n{digest}".encode()
            signature = hmac.new(
                self.valves.hmac_secret.encode(), canonical, hashlib.sha256
            ).hexdigest()
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
            status_payload: Any = response.json()
            if not isinstance(status_payload, dict) or set(status_payload) != {
                "status",
                "queued_jobs",
                "dead_jobs",
            }:
                return self._UNAVAILABLE
            queued_jobs = status_payload["queued_jobs"]
            dead_jobs = status_payload["dead_jobs"]
            if (
                status_payload["status"] != "ok"
                or type(queued_jobs) is not int
                or type(dead_jobs) is not int
                or queued_jobs < 0
                or dead_jobs < 0
            ):
                return self._UNAVAILABLE
            return (
                f"Assistant Core: ok; queued jobs: {queued_jobs}; dead jobs: {dead_jobs}."
            )
        except Exception:  # noqa: BLE001 - optional status must fail closed to unavailable.
            return self._UNAVAILABLE
