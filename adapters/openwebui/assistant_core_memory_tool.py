"""
title: Assistant Core Memory & Preferences
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
    """Explicit tool for inspecting, saving, updating, and removing durable user memories."""

    _UNAVAILABLE = "Assistant Core Memory Service is currently unavailable."

    class Valves(BaseModel):
        """Administrator-managed companion connection settings."""

        assistant_core_url: str = Field(
            default="http://assistant-core:8080",
            description="Assistant Core API backend URL",
        )
        hmac_secret: str = Field(
            default="development-hmac-secret-change-me",
            description="Shared HMAC secret matching ASSISTANT_HMAC_SECRET",
            json_schema_extra={"input": {"type": "password"}},
        )
        timeout_seconds: float = Field(
            default=5.0,
            ge=0.5,
            le=30.0,
            description="Request timeout in seconds",
        )

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
        """POST one JSON body over the signed Assistant Core transport."""
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

    async def list_memories(
        self,
        query: str = "",
        category: str = "",
        limit: int = 10,
        __user__: dict | None = None,
    ) -> str:
        """Search and list currently active user memories by keyword, key, or category.
        
        :param query: Optional keyword search across statements and keys.
        :param category: Optional domain category filter (e.g. 'career', 'infrastructure', 'preference', 'fact', 'homelab').
        :param limit: Maximum memories to return (1-50, default 10).
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            payload: dict[str, object] = {
                "native_user_id": native_user_id,
                "limit": max(1, min(limit, 50)),
            }
            if query and query.strip():
                payload["query"] = query.strip()
            if category and category.strip():
                payload["category"] = category.strip().lower()

            res = await self._signed_json_post("/v1/personal-context/memory/list", payload)
            if not isinstance(res, dict) or "memories" not in res:
                return self._UNAVAILABLE

            memories = res.get("memories", [])
            if not memories:
                filter_desc = []
                if query:
                    filter_desc.append(f"matching '{query}'")
                if category:
                    filter_desc.append(f"in category '{category}'")
                desc_str = f" {' '.join(filter_desc)}" if filter_desc else ""
                return f"No active memories found{desc_str}."

            lines = [f"🧠 Active User Memories ({len(memories)} found):"]
            for m in memories:
                if not isinstance(m, dict):
                    continue
                m_id = m.get("id", "?")
                key = m.get("key", "note")
                cat = m.get("category", "fact")
                statement = m.get("statement", "")
                tag = m.get("temporal_tag")
                exp = m.get("expires_at")

                meta_parts = [f"category: {cat}"]
                if tag:
                    meta_parts.append(f"tag: {tag}")
                if exp:
                    meta_parts.append(f"expires: {exp[:10]}")
                meta_str = f" [{', '.join(meta_parts)}]"

                lines.append(f"- **`{key}`** (`{m_id}`){meta_str}:\n  ↳ {statement}")

            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            return f"Failed to list memories: {exc}"

    async def save_memory(
        self,
        key: str,
        statement: str,
        category: str = "fact",
        temporal_tag: str = "",
        expires_at: str = "",
        __user__: dict | None = None,
    ) -> str:
        """Save a new memory or replace an existing memory key with updated facts/preferences.
        
        :param key: Dotted lowercase identifier (e.g. 'career.current_job', 'preferences.editor', 'infra.traefik').
        :param statement: The authoritative statement to remember (e.g. 'User prefers neovim with tmux on Linux').
        :param category: Domain category slug (e.g. 'career', 'infrastructure', 'homelab', 'preference', 'fact', 'learning').
        :param temporal_tag: Optional lifecycle tag for in-progress tasks (e.g. 'in_progress', 'job_application', 'temporary').
        :param expires_at: Optional ISO 8601 expiration date string (e.g. '2026-10-01T00:00:00Z').
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            if not key or not key.strip():
                return "Error: key cannot be empty"
            if not statement or not statement.strip():
                return "Error: statement cannot be empty"

            payload: dict[str, object] = {
                "native_user_id": native_user_id,
                "key": key.strip(),
                "statement": statement.strip(),
                "category": category.strip().lower() or "fact",
            }
            if temporal_tag and temporal_tag.strip():
                payload["temporal_tag"] = temporal_tag.strip().lower()
            if expires_at and expires_at.strip():
                payload["expires_at"] = expires_at.strip()

            res = await self._signed_json_post("/v1/personal-context/memory/save", payload)
            if not isinstance(res, dict) or "memory" not in res:
                return self._UNAVAILABLE

            mem = res["memory"]
            action = "Saved new" if res.get("created") else "Updated"
            return (
                f"✅ {action} memory `{mem.get('key')}` (ID: `{mem.get('id')}`)\n"
                f"- Category: `{mem.get('category')}`\n"
                f"- Statement: {mem.get('statement')}\n"
                + (f"- Validity: tag=`{mem.get('temporal_tag')}` expires=`{mem.get('expires_at')}`\n" if mem.get('temporal_tag') or mem.get('expires_at') else "")
            )
        except Exception as exc:  # noqa: BLE001
            return f"Failed to save memory: {exc}"

    async def update_memory(
        self,
        memory_id: str,
        statement: str = "",
        category: str = "",
        temporal_tag: str = "",
        expires_at: str = "",
        clear_expiration: bool = False,
        __user__: dict | None = None,
    ) -> str:
        """Modify an existing memory record by its UUID (refine statement, change category, or adjust validity).
        
        :param memory_id: UUID of the memory record to update.
        :param statement: Optional new statement text.
        :param category: Optional new category slug (e.g. 'career', 'infrastructure', 'homelab').
        :param temporal_tag: Optional new temporal tag.
        :param expires_at: Optional new ISO 8601 expiration timestamp.
        :param clear_expiration: If True, removes any expiration date making the memory permanent.
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            if not memory_id or not memory_id.strip():
                return "Error: memory_id cannot be empty"

            payload: dict[str, object] = {
                "native_user_id": native_user_id,
                "memory_id": memory_id.strip(),
                "clear_expiration": clear_expiration,
            }
            if statement and statement.strip():
                payload["statement"] = statement.strip()
            if category and category.strip():
                payload["category"] = category.strip().lower()
            if temporal_tag and temporal_tag.strip():
                payload["temporal_tag"] = temporal_tag.strip().lower()
            if expires_at and expires_at.strip():
                payload["expires_at"] = expires_at.strip()

            res = await self._signed_json_post("/v1/personal-context/memory/update", payload)
            if not isinstance(res, dict) or "memory" not in res:
                return self._UNAVAILABLE

            mem = res["memory"]
            return (
                f"✅ Updated memory `{mem.get('key')}` (ID: `{mem.get('id')}`)\n"
                f"- Category: `{mem.get('category')}`\n"
                f"- Statement: {mem.get('statement')}\n"
                + (f"- Temporal Tag: `{mem.get('temporal_tag')}`\n" if mem.get('temporal_tag') else "")
                + (f"- Expires At: `{mem.get('expires_at')}`\n" if mem.get('expires_at') else "")
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return f"Memory `{memory_id}` not found or not active."
            return f"Failed to update memory: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to update memory: {exc}"

    async def forget_memory(
        self,
        memory_id: str = "",
        key: str = "",
        reason: str = "",
        __user__: dict | None = None,
    ) -> str:
        """Archive and forget an obsolete, superseded, or unwanted memory by ID or key.
        
        :param memory_id: UUID of the memory to forget.
        :param key: Or the dotted key (e.g. 'career.current_job') to archive all matching active entries.
        :param reason: Optional reason for forgetting (e.g. 'Project completed', 'Fact was invalidated').
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            if not memory_id and not key:
                return "Error: either memory_id or key must be provided"

            payload: dict[str, object] = {
                "native_user_id": native_user_id,
            }
            if memory_id and memory_id.strip():
                payload["memory_id"] = memory_id.strip()
            if key and key.strip():
                payload["key"] = key.strip().lower()
            if reason and reason.strip():
                payload["reason"] = reason.strip()

            res = await self._signed_json_post("/v1/personal-context/memory/forget", payload)
            if not isinstance(res, dict):
                return self._UNAVAILABLE

            count = res.get("archived_count", 0)
            reason_str = f" (Reason: {reason})" if reason else ""
            target = f"ID `{memory_id}`" if memory_id else f"key `{key}`"
            return f"🗑️ Forgotten / archived {count} memory matching {target}{reason_str}."
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return f"No active memory found matching {'ID ' + memory_id if memory_id else 'key ' + key}."
            return f"Failed to forget memory: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to forget memory: {exc}"
