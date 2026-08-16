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

    async def _signed_json_post(self, path: str, payload: dict[str, object]) -> Any:
        """POST one JSON body over the existing signed Assistant Core transport."""
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
        return response.json()

    @staticmethod
    def _context_payload(
        __user__: dict | None, __metadata__: dict | None
    ) -> dict[str, object]:
        """Build the bounded native identity envelope used by context routes."""
        return {
            "native_user_id": Tools._optional_id(__user__, "id") or "unknown",
            "native_chat_id": Tools._optional_id(__metadata__, "chat_id"),
            "native_message_id": Tools._optional_id(__metadata__, "message_id"),
        }

    async def show_loaded_profile(
        self,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Display the frozen current-chat profile and its source IDs."""
        try:
            payload = await self._signed_json_post(
                "/v1/context", self._context_payload(__user__, __metadata__)
            )
            if not isinstance(payload, dict) or set(payload) != {
                "context_text",
                "token_estimate",
                "sources",
                "degraded",
            }:
                return self._UNAVAILABLE
            context_text = payload["context_text"]
            token_estimate = payload["token_estimate"]
            sources = payload["sources"]
            degraded = payload["degraded"]
            if (
                type(context_text) is not str
                or type(token_estimate) is not int
                or token_estimate < 0
                or type(degraded) is not bool
                or type(sources) is not list
            ):
                return self._UNAVAILABLE
            source_ids: list[str] = []
            for source in sources:
                if not isinstance(source, dict) or set(source) != {
                    "source_type",
                    "source_id",
                    "label",
                } or any(type(source[key]) is not str for key in source):
                    return self._UNAVAILABLE
                source_ids.append(source["source_id"])
            if degraded:
                return self._UNAVAILABLE
            return (
                f"{context_text}\n"
                f"Profile source IDs: {', '.join(source_ids) if source_ids else 'none'}."
            )
        except Exception:  # noqa: BLE001 - optional profile must fail open.
            return self._UNAVAILABLE

    async def search_personal_context(
        self,
        query: str,
        limit: int = 5,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Search durable memory and past conversation evidence before personal claims."""
        try:
            payload = self._context_payload(__user__, __metadata__)
            payload.update({"query": query.strip(), "limit": limit})
            response = await self._signed_json_post("/v1/personal-context/search", payload)
            if not isinstance(response, dict) or set(response) != {"mode", "results"}:
                return self._UNAVAILABLE
            results = response["results"]
            mode = response["mode"]
            if mode not in {"lexical", "hybrid"} or type(results) is not list:
                return self._UNAVAILABLE
            lines: list[str] = []
            for result in results:
                if not isinstance(result, dict) or set(result) != {
                    "source_id",
                    "source_type",
                    "category",
                    "role",
                    "preview",
                    "source_native_chat_id",
                    "source_native_message_id",
                }:
                    return self._UNAVAILABLE
                if any(
                    type(result[key]) is not str
                    for key in ("source_id", "source_type", "category", "preview")
                ):
                    return self._UNAVAILABLE
                source_type = result["source_type"]
                role = result["role"]
                chat_id = result["source_native_chat_id"]
                message_id = result["source_native_message_id"]
                if source_type == "memory":
                    if role is not None or chat_id is not None or message_id is not None:
                        return self._UNAVAILABLE
                    label = f"memory/{result['category']}"
                    provenance = ""
                elif source_type == "conversation":
                    if (
                        role not in {"user", "assistant"}
                        or type(chat_id) is not str
                        or type(message_id) is not str
                    ):
                        return self._UNAVAILABLE
                    label = f"conversation/{role} evidence"
                    provenance = f" (chat {chat_id}, message {message_id})"
                else:
                    return self._UNAVAILABLE
                lines.append(
                    f"- [{label}] {result['source_id']}: {result['preview']}{provenance}"
                )
            if not lines:
                return "No matching personal context found."
            return f"Personal context search ({mode}):\n" + "\n".join(lines)
        except Exception:  # noqa: BLE001 - optional memory search must fail open.
            return self._UNAVAILABLE

    async def read_personal_context(
        self,
        memory_source_id: str,
        __user__: dict | None = None,
    ) -> str:
        """Expand one memory or conversation source with bounded provenance."""
        try:
            payload = {
                "native_user_id": self._optional_id(__user__, "id") or "unknown",
                "memory_source_id": memory_source_id,
            }
            response = await self._signed_json_post("/v1/personal-context/read", payload)
            expected = {
                "source_id",
                "source_type",
                "content",
                "category",
                "role",
                "evidence_quote",
                "source_native_chat_id",
                "source_native_message_id",
                "neighbors",
                "full_source_available",
            }
            if not isinstance(response, dict) or set(response) != expected:
                return self._UNAVAILABLE
            text_fields = (
                "source_id",
                "source_type",
                "content",
                "category",
                "source_native_chat_id",
                "source_native_message_id",
            )
            if (
                any(type(response[field]) is not str for field in text_fields)
                or type(response["full_source_available"]) is not bool
                or type(response["neighbors"]) is not list
            ):
                return self._UNAVAILABLE
            source_type = response["source_type"]
            role = response["role"]
            evidence_quote = response["evidence_quote"]
            if source_type == "memory":
                if role is not None or type(evidence_quote) is not str:
                    return self._UNAVAILABLE
            elif source_type == "conversation":
                if role not in {"user", "assistant"} or evidence_quote is not None:
                    return self._UNAVAILABLE
            else:
                return self._UNAVAILABLE
            neighbor_lines: list[str] = []
            for neighbor in response["neighbors"]:
                if not isinstance(neighbor, dict) or set(neighbor) != {
                    "role",
                    "content",
                    "source_native_chat_id",
                    "source_native_message_id",
                } or any(type(value) is not str for value in neighbor.values()):
                    return self._UNAVAILABLE
                if neighbor["role"] not in {"user", "assistant"}:
                    return self._UNAVAILABLE
                neighbor_lines.append(
                    f"- {neighbor['role']} ({neighbor['source_native_message_id']}): "
                    f"{neighbor['content']}"
                )
            source_label = "memory" if source_type == "memory" else "conversation"
            role_line = f"Role: {role}\n" if role is not None else ""
            evidence_line = (
                f"Evidence: \"{evidence_quote}\"\n" if evidence_quote is not None else ""
            )
            neighbor_text = (
                "\n".join(neighbor_lines) if neighbor_lines else "none"
            )
            return (
                f"Personal {source_label} source {response['source_id']}:\n"
                f"Content: {response['content']}\n"
                f"Category: {response['category']}\n"
                f"{role_line}"
                f"{evidence_line}"
                f"Source chat: {response['source_native_chat_id']}\n"
                f"Source message: {response['source_native_message_id']}\n"
                f"Neighboring context: {neighbor_text}\n"
                "Full-source expansion available: "
                f"{'yes' if response['full_source_available'] else 'no'}"
            )
        except Exception:  # noqa: BLE001 - optional memory read must fail open.
            return self._UNAVAILABLE

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

    async def show_recent_conversations(
        self,
        limit: int = 5,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Show the most recent conversation references without content."""
        if not isinstance(limit, int) or limit < 1 or limit > 10:
            limit = 5
        native_user_id = self._optional_id(__user__, "id") or "unknown"
        payload = {
            "native_user_id": native_user_id,
            "native_chat_id": self._optional_id(__metadata__, "chat_id"),
            "native_message_id": self._optional_id(__metadata__, "message_id"),
            "limit": limit,
        }
        try:
            data = await self._signed_json_post("/v1/inspection/recent", payload)
            if not isinstance(data, dict) or "results" not in data:
                return self._UNAVAILABLE
            results = data["results"]
            if not isinstance(results, list):
                return self._UNAVAILABLE
            if not results:
                return "No indexed conversation references for this user yet."
            lines = [f"Recent {len(results)} indexed references:"]
            for item in results:
                if not isinstance(item, dict):
                    continue
                ref_id = item.get("reference_id", "?")
                chat_id = item.get("native_chat_id", "?")
                msg_id = item.get("native_message_id", "?")
                role = item.get("role", "?")
                ordinal = item.get("chunk_ordinal", "?")
                tomb = item.get("tombstoned_at")
                status = "tombstoned" if tomb else "active"
                lines.append(
                    f"- {ref_id[:8]}… chat {str(chat_id)[:8]}… msg {str(msg_id)[:8]}… role {role} ord {ordinal} {status}"
                )
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return self._UNAVAILABLE

    async def show_index_stats(
        self,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Show aggregate conversation index counters without content."""
        native_user_id = self._optional_id(__user__, "id") or "unknown"
        payload = {
            "native_user_id": native_user_id,
            "native_chat_id": self._optional_id(__metadata__, "chat_id"),
            "native_message_id": self._optional_id(__metadata__, "message_id"),
        }
        try:
            data = await self._signed_json_post("/v1/inspection/stats", payload)
            if not isinstance(data, dict):
                return self._UNAVAILABLE
            required = {
                "total_segments",
                "embedded_segments",
                "lexical_segments",
                "total_references",
                "active_references",
                "tombstoned_references",
                "queued_jobs",
                "dead_jobs",
            }
            if not required.issubset(data.keys()):
                return self._UNAVAILABLE
            last = data.get("last_indexed_at")
            last_str = str(last) if last else "never"
            return (
                f"Index stats: segments {data['total_segments']} "
                f"(embedded {data['embedded_segments']}, lexical {data['lexical_segments']}), "
                f"references {data['total_references']} "
                f"(active {data['active_references']}, tombstoned {data['tombstoned_references']}), "
                f"jobs queued {data['queued_jobs']} dead {data['dead_jobs']}, "
                f"last indexed {last_str}."
            )
        except Exception:  # noqa: BLE001
            return self._UNAVAILABLE
