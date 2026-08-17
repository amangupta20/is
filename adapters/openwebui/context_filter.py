"""
title: Assistant Context
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
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field, field_validator


class Filter:
    """Fail-open Open WebUI Filter for optional companion context."""

    _EVENT_PATH = "/v1/events"
    _EVENT_SOURCE = "openwebui_outlet_filter"
    _MAX_EVENT_BYTES = 524_288
    _UNSAVED_CHAT_PREFIXES = ("temporary:", "local:", "channel:")

    class Valves(BaseModel):
        """Administrator-managed companion connection settings."""

        assistant_core_url: str = Field(default="http://assistant-core:8080")
        hmac_secret: str = Field(
            default="development-hmac-secret-change-me",
            json_schema_extra={"input": {"type": "password"}},
        )
        timeout_seconds: float = Field(default=1.5, ge=0.1, le=5.0)
        max_context_tokens: str = Field(default="300000")
        priority: int = Field(default=-100)
        user_timezone: str = Field(
            default="Asia/Kolkata",
            description="IANA timezone identifier for real-time datetime anchor (e.g. Asia/Kolkata for IST, America/New_York, UTC)",
        )
        inject_temporal_anchor: bool = Field(
            default=True,
            description="Inject real-world date, time, day of week, and timezone into assistant context",
        )

        @field_validator("max_context_tokens", mode="before")
        @classmethod
        def normalize_max_context_tokens(cls, value: object) -> str:
            """Accept canonical text budgets and normalize legacy integer values."""
            if type(value) is int:
                normalized = str(value)
            elif type(value) is str:
                normalized = value
            else:
                raise ValueError("max_context_tokens must be a decimal string or plain integer")

            if not (
                normalized == "0"
                or (
                    normalized.isascii() and normalized.isdigit() and not normalized.startswith("0")
                )
            ):
                raise ValueError("max_context_tokens must be a canonical decimal string")

            if int(normalized) > 500_000:
                raise ValueError("max_context_tokens must not exceed 500000")

            return normalized

    def __init__(self) -> None:
        """Initialise the Filter with its administrator-configured valves."""
        self.valves = self.Valves()

    @staticmethod
    def _event_bytes(payload: Mapping[str, object]) -> bytes:
        """Return the canonical compact, sorted UTF-8 event representation."""
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    async def _post_signed(self, path: str, payload: dict[str, object]) -> dict:
        """Send one exactly serialized and signed request to the companion service."""
        if path == "/v1/context":
            request_body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        else:
            request_body = self._event_bytes(payload)
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

    async def _post_context(self, payload: dict) -> dict:
        """Send one exactly signed request to the companion service."""
        return await self._post_signed("/v1/context", payload)

    @staticmethod
    def _plain_id(value: object) -> str | None:
        if type(value) is not str or not value.strip() or len(value) > 200:
            return None
        return value

    @classmethod
    def _captured_message(
        cls,
        message: Mapping[object, object],
        expected_id: str,
        expected_role: str,
    ) -> dict[str, object] | None:
        if cls._plain_id(message.get("id")) != expected_id:
            return None
        if message.get("role") != expected_role:
            return None
        content = message.get("content")
        if type(content) is not str or (expected_role == "user" and not content.strip()):
            return None

        captured: dict[str, object] = {
            "id": expected_id,
            "role": expected_role,
            "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        if "timestamp" in message:
            timestamp = message["timestamp"]
            if type(timestamp) is not int or timestamp < 0:
                return None
            captured["timestamp"] = timestamp
        return captured

    @classmethod
    def _turn_event_id(cls, user_id: str, chat_id: str, assistant_id: str) -> str:
        identity = cls._event_bytes(
            {
                "native_chat_id": chat_id,
                "native_message_id": assistant_id,
                "native_user_id": user_id,
                "source": cls._EVENT_SOURCE,
            }
        )
        return f"turn:v1:{hashlib.sha256(identity).hexdigest()}"

    @staticmethod
    def _diagnose_turn_failure(
        event_id: str,
        event_type: str,
        user_content_bytes: int,
        assistant_content_bytes: int,
    ) -> None:
        diagnostic = {
            "failure": "assistant_core_turn_delivery_failed",
            "event_id": event_id,
            "event_type": event_type,
            "user_content_bytes": user_content_bytes,
            "assistant_content_bytes": assistant_content_bytes,
        }
        print(
            json.dumps(diagnostic, separators=(",", ":"), sort_keys=True),
            file=sys.stderr,
        )

    @classmethod
    def _extract_scope_ids(
        cls, body: Mapping[object, object] | None, metadata: Mapping[object, object] | None
    ) -> tuple[str | None, str | None]:
        folder_id: str | None = None
        project_id: str | None = None
        if isinstance(metadata, Mapping):
            folder_id = cls._plain_id(metadata.get("folder_id"))
            project_id = cls._plain_id(metadata.get("project_id"))
            chat = metadata.get("chat")
            if folder_id is None and isinstance(chat, Mapping):
                folder_id = cls._plain_id(chat.get("folder_id"))
            if project_id is None and isinstance(chat, Mapping):
                project_id = cls._plain_id(chat.get("project_id"))
        if folder_id is None and isinstance(body, Mapping):
            folder_id = cls._plain_id(body.get("folder_id"))
            chat = body.get("chat")
            if folder_id is None and isinstance(chat, Mapping):
                folder_id = cls._plain_id(chat.get("folder_id"))
        if project_id is None and isinstance(body, Mapping):
            project_id = cls._plain_id(body.get("project_id"))
            chat = body.get("chat")
            if project_id is None and isinstance(chat, Mapping):
                project_id = cls._plain_id(chat.get("project_id"))
        return folder_id, project_id

    def _format_temporal_anchor(self) -> str:
        """Format real-world datetime anchor in user's configured timezone (e.g. IST)."""
        if not self.valves.inject_temporal_anchor:
            return ""
        try:
            tz = ZoneInfo(self.valves.user_timezone)
        except Exception:  # noqa: BLE001
            tz = ZoneInfo("Asia/Kolkata")
        local_now = datetime.now(tz)
        formatted_time = local_now.strftime("%A, %d %B %Y, %I:%M %p %Z")
        return (
            "<current_datetime>\n"
            f"Current Real-World Time: {formatted_time} ({self.valves.user_timezone})\n"
            f"Temporal Grounding: Today is {local_now.strftime('%A, %d %B %Y')}. "
            "All relative timeframes (such as 'today', 'yesterday', 'this week', 'last week', "
            "'in X days', deadlines, and elapsed time between user sessions) must be "
            "evaluated against this real-world date and time.\n"
            "</current_datetime>"
        )

    async def inlet(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> dict:
        """Insert optional context at the cache-stable system-prefix boundary."""
        messages = body.get("messages")
        if not isinstance(messages, list) or not __user__ or "id" not in __user__:
            return body

        latest_user_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], Mapping) and messages[index].get("role") == "user"
            ),
            None,
        )
        if latest_user_index is None:
            return body

        metadata = __metadata__ or {}
        request_text = str(messages[latest_user_index].get("content", ""))[:16_000]
        folder_id, project_id = self._extract_scope_ids(body, metadata)
        context_text: str | None = None
        try:
            response = await self._post_context(
                {
                    "native_user_id": str(__user__["id"]),
                    "native_chat_id": metadata.get("chat_id"),
                    "native_project_id": project_id,
                    "native_folder_id": folder_id,
                    "native_message_id": metadata.get("message_id"),
                    "request_text": request_text,
                    "max_tokens": int(self.valves.max_context_tokens),
                }
            )
            context_text = response.get("context_text") if isinstance(response, dict) else None
        except Exception:  # noqa: BLE001 - optional context must fail open for every companion failure.
            context_text = None

        temporal_anchor = self._format_temporal_anchor()

        sections: list[str] = []
        if temporal_anchor:
            sections.append(temporal_anchor)
        if isinstance(context_text, str) and context_text.strip():
            sections.append(context_text.strip())

        if not sections:
            return body

        rendered_context = f"<assistant_context>\n{chr(10).join(sections)}\n</assistant_context>"

        for msg in messages:
            if (
                isinstance(msg, Mapping)
                and msg.get("role") == "system"
                and isinstance(msg.get("content"), str)
                and msg["content"].startswith("<assistant_context>\n")
            ):
                msg["content"] = rendered_context
                return body

        prefix_end = 0
        while (
            prefix_end < len(messages)
            and isinstance(messages[prefix_end], Mapping)
            and messages[prefix_end].get("role") == "system"
        ):
            prefix_end += 1

        messages.insert(
            prefix_end,
            {
                "role": "system",
                "content": rendered_context,
            },
        )
        return body

    async def outlet(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        **_kwargs: Any,
    ) -> dict:
        """Capture one completed saved-chat turn without changing native output."""
        diagnostic_event_id: str | None = None
        diagnostic_event_type = "turn.completed.v1"
        user_content_bytes = 0
        assistant_content_bytes = 0
        try:
            if not isinstance(body, Mapping):
                return body
            if not isinstance(__user__, Mapping) or not isinstance(__metadata__, Mapping):
                return body

            user_id = self._plain_id(__user__.get("id"))
            metadata_user_id = self._plain_id(__metadata__.get("user_id"))
            chat_id = self._plain_id(body.get("chat_id"))
            metadata_chat_id = self._plain_id(__metadata__.get("chat_id"))
            assistant_id = self._plain_id(body.get("id"))
            metadata_message_id = self._plain_id(__metadata__.get("message_id"))
            user_message_id = self._plain_id(__metadata__.get("user_message_id"))
            if (
                user_id is None
                or user_id != metadata_user_id
                or chat_id is None
                or chat_id != metadata_chat_id
                or chat_id.startswith(self._UNSAVED_CHAT_PREFIXES)
                or assistant_id is None
                or assistant_id != metadata_message_id
                or user_message_id is None
                or user_message_id == assistant_id
            ):
                return body

            messages = body.get("messages")
            if not isinstance(messages, list):
                return body
            user_matches = [
                message
                for message in messages
                if isinstance(message, Mapping) and message.get("id") == user_message_id
            ]
            assistant_matches = [
                message
                for message in messages
                if isinstance(message, Mapping) and message.get("id") == assistant_id
            ]
            if len(user_matches) != 1 or len(assistant_matches) != 1:
                return body

            user_message = self._captured_message(user_matches[0], user_message_id, "user")
            assistant_message = self._captured_message(
                assistant_matches[0], assistant_id, "assistant"
            )
            if user_message is None or assistant_message is None:
                return body

            event_id = self._turn_event_id(user_id, chat_id, assistant_id)
            diagnostic_event_id = event_id
            user_content_bytes = len(user_message["content"].encode("utf-8"))
            assistant_content_bytes = len(assistant_message["content"].encode("utf-8"))
            occurred_at = datetime.now(UTC).isoformat(timespec="microseconds")

            attached_file_ids: list[str] = []
            candidate_files: list[object] = []
            # Only examine explicit user message attachments, never body-level files (which contain auto-retrieved RAG / KB docs)
            if isinstance(user_matches[0].get("files"), list):
                candidate_files.extend(user_matches[0]["files"])

            for f in candidate_files:
                if isinstance(f, Mapping):
                    ftype = f.get("type")
                    if isinstance(ftype, str) and ftype in (
                        "collection",
                        "knowledge",
                        "web",
                        "note",
                        "folder",
                        "doc",
                    ):
                        continue
                    if (
                        f.get("collection_name")
                        or f.get("knowledge_id")
                        or f.get("collection_id")
                        or f.get("kb_id")
                        or f.get("knowledge")
                    ):
                        continue
                    fmeta = f.get("meta")
                    if isinstance(fmeta, Mapping) and (
                        fmeta.get("collection_name")
                        or fmeta.get("knowledge_id")
                        or fmeta.get("collection_id")
                        or fmeta.get("kb_id")
                        or fmeta.get("knowledge")
                        or fmeta.get("source") in ("knowledge", "collection", "rag", "external")
                    ):
                        continue
                    fid = f.get("id") or f.get("file_id")
                    if (
                        isinstance(fid, str)
                        and fid.strip()
                        and fid.strip() not in attached_file_ids
                    ):
                        attached_file_ids.append(fid.strip())

            turn_payload: dict[str, object] = {
                "source": self._EVENT_SOURCE,
                "user_message": user_message,
                "assistant_message": assistant_message,
            }
            if attached_file_ids:
                turn_payload["attached_file_ids"] = attached_file_ids

            folder_id, project_id = self._extract_scope_ids(body, __metadata__)
            envelope: dict[str, object] = {
                "schema_version": 1,
                "event_id": event_id,
                "event_type": "turn.completed.v1",
                "occurred_at": occurred_at,
                "native_user_id": user_id,
                "native_chat_id": chat_id,
                "native_project_id": project_id,
                "native_folder_id": folder_id,
                "native_message_id": assistant_id,
                "payload": turn_payload,
            }
            if len(self._event_bytes(envelope)) > self._MAX_EVENT_BYTES:
                diagnostic_event_type = "turn.oversized.v1"
                envelope = {
                    "schema_version": 1,
                    "event_id": event_id,
                    "event_type": "turn.oversized.v1",
                    "occurred_at": occurred_at,
                    "native_user_id": user_id,
                    "native_chat_id": chat_id,
                    "native_project_id": project_id,
                    "native_folder_id": folder_id,
                    "native_message_id": assistant_id,
                    "payload": {
                        "source": self._EVENT_SOURCE,
                        "user_message": {
                            "id": user_message_id,
                            "sha256": user_message["sha256"],
                            "content_bytes": user_content_bytes,
                        },
                        "assistant_message": {
                            "id": assistant_id,
                            "sha256": assistant_message["sha256"],
                            "content_bytes": assistant_content_bytes,
                        },
                    },
                }
            await self._post_signed(self._EVENT_PATH, envelope)
        except Exception:  # noqa: BLE001 - turn capture must fail open.
            if diagnostic_event_id is not None:
                try:
                    self._diagnose_turn_failure(
                        diagnostic_event_id,
                        diagnostic_event_type,
                        user_content_bytes,
                        assistant_content_bytes,
                    )
                except Exception:  # noqa: BLE001 - diagnostics are best-effort only.
                    return body
            return body
        return body
