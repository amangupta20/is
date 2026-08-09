"""
title: Assistant Core Lifecycle
version: 0.1.0
requirements: httpx
"""

import hashlib
import hmac
import json
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field


class Event:
    """Fail-open forwarding for selected stable Open WebUI lifecycle events."""

    _EVENT_NAMES = frozenset(
        {
            "chat.finished",
            "chat.deleted",
            "chat.compacted",
            "message.created",
            "file.uploaded",
            "file.deleted",
            "user.deleted",
        }
    )
    _DIAGNOSTIC_LIMIT = 80

    class Valves(BaseModel):
        """Administrator-managed companion connection settings."""

        assistant_core_url: str = Field(default="http://assistant-core:8080")
        hmac_secret: str = Field(
            default="development-hmac-secret-change-me",
            json_schema_extra={"input": {"type": "password"}},
        )
        timeout_seconds: float = Field(default=2.0, ge=0.1, le=10.0)

    def __init__(self) -> None:
        """Initialise the Event Function with administrator-configured valves."""
        self.valves = self.Valves()

    @staticmethod
    def _id_from(value: object) -> str | None:
        if not isinstance(value, Mapping):
            return None
        identifier = value.get("id")
        return Event._scalar_id(identifier)

    @staticmethod
    def _scalar_id(value: object) -> str | None:
        if type(value) is str:
            return value
        if type(value) is int:
            return str(value)
        return None

    @classmethod
    def _data_id(cls, event: Mapping[object, object], key: str) -> str | None:
        data = event.get("data")
        if not isinstance(data, Mapping):
            return None
        return cls._scalar_id(data.get(key))

    def _diagnose_failure(self, event_name: str, event_id: str) -> None:
        diagnostic = {
            "failure": "assistant_core_event_delivery_failed",
            "event_name": event_name,
            "event_id": event_id,
        }
        print(json.dumps(diagnostic, separators=(",", ":"), sort_keys=True), file=sys.stderr)

    async def event(
        self,
        event: object,
        __event_id__: str | None = None,
        __event_name__: str | None = None,
        **_kwargs: Any,
    ) -> None:
        """Forward an allowlisted stable event without blocking Open WebUI."""
        if type(__event_name__) is not str or type(__event_id__) is not str or not __event_id__:
            return
        safe_event_name = __event_name__[: self._DIAGNOSTIC_LIMIT]
        safe_event_id = __event_id__[: self._DIAGNOSTIC_LIMIT]

        try:
            if __event_name__ not in self._EVENT_NAMES or not isinstance(event, Mapping):
                return

            actor_id = self._id_from(event.get("actor"))
            legacy_user_id = self._id_from(event.get("user"))
            subject_id = self._id_from(event.get("subject"))
            legacy_chat_id = self._id_from(event.get("chat"))
            legacy_message_id = self._id_from(event.get("message"))
            legacy_file_id = self._id_from(event.get("file"))

            if __event_name__ == "user.deleted":
                native_user_id = subject_id or legacy_user_id
            elif __event_name__ == "chat.deleted":
                native_user_id = self._data_id(event, "owner_id") or actor_id or legacy_user_id
            elif __event_name__ == "chat.finished":
                native_user_id = actor_id or legacy_user_id or self._data_id(event, "user_id")
            else:
                native_user_id = actor_id or legacy_user_id
            if not native_user_id:
                return

            payload = {"source": "openwebui_event"}
            chat_id = legacy_chat_id
            message_id = legacy_message_id
            file_id = legacy_file_id
            if __event_name__ == "message.created":
                chat_id = self._data_id(event, "chat_id") or legacy_chat_id
                message_id = subject_id or legacy_message_id
            elif __event_name__ == "chat.finished":
                chat_id = subject_id or legacy_chat_id
                message_id = self._data_id(event, "message_id") or legacy_message_id
            elif __event_name__ in {"chat.deleted", "chat.compacted"}:
                chat_id = subject_id or legacy_chat_id
            elif __event_name__ in {"file.uploaded", "file.deleted"}:
                file_id = subject_id or legacy_file_id
            if file_id is not None:
                payload["file_id"] = file_id

            envelope = {
                "schema_version": 1,
                "event_id": __event_id__,
                "event_type": __event_name__,
                "occurred_at": datetime.now(UTC).isoformat(),
                "native_user_id": native_user_id,
                "native_chat_id": chat_id,
                "native_message_id": message_id,
                "payload": payload,
            }

            path = "/v1/events"
            request_body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
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
        except Exception:  # noqa: BLE001 - lifecycle delivery must fail open.
            try:
                self._diagnose_failure(safe_event_name, safe_event_id)
            except Exception:  # noqa: BLE001 - diagnostics are best-effort and fail open.
                return
