"""
title: Assistant File Index
version: 0.1.0
requirements: httpx
"""

import hashlib
import hmac
import json
import sys
import time
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, Field


class Filter:
    """Fail-open file indexer that forwards Library/chat files to Assistant Core."""

    class Valves(BaseModel):
        """Connection to the companion."""

        assistant_core_url: str = Field(default="http://assistant-core:8080")
        hmac_secret: str = Field(
            default="development-hmac-secret-change-me",
            json_schema_extra={"input": {"type": "password"}},
        )
        timeout_seconds: float = Field(default=2.0, ge=0.1, le=5.0)
        open_webui_url: str = Field(default="http://open-webui:8080")
        open_webui_api_key: str = Field(
            default="",
            json_schema_extra={"input": {"type": "password"}},
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    @staticmethod
    def _optional_id(container: object, key: str) -> str | None:
        if not isinstance(container, Mapping):
            return None
        value = container.get(key)
        return str(value) if isinstance(value, str) and value else None

    def _signed_post(self, path: str, payload: dict[str, object]) -> None:
        """Fire-and-forget signed POST, fail open."""
        try:
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            if len(body) > 500_000:
                return
            timestamp = str(int(time.time()))
            digest = hashlib.sha256(body).hexdigest()
            canonical = f"POST\n{path}\n{timestamp}\n{digest}".encode()
            signature = hmac.new(
                self.valves.hmac_secret.encode(), canonical, hashlib.sha256
            ).hexdigest()
            url = f"{self.valves.assistant_core_url.rstrip('/')}{path}"
            headers = {
                "content-type": "application/json",
                "x-assistant-timestamp": timestamp,
                "x-assistant-signature": signature,
            }
            # Use sync httpx for Open WebUI's sync filter path; fail open on any error.
            with httpx.Client(timeout=self.valves.timeout_seconds) as client:
                resp = client.post(url, content=body, headers=headers)
                if resp.status_code >= 400:
                    print(
                        f"file_index_filter: assistant-core {path} {resp.status_code}",
                        file=sys.stderr,
                    )
        except Exception as exc:  # noqa: BLE001 - must not break chat
            print(f"file_index_filter: fail-open {exc}", file=sys.stderr)

    def inlet(self, body: dict[str, Any], __user__: dict | None = None) -> dict[str, Any]:
        """Index any files attached to the current turn."""
        try:
            user_id = self._optional_id(__user__, "id")
            if not user_id:
                return body

            # Open WebUI passes files in body["files"] (Library) and in messages[].files
            candidates: list[Mapping[str, object]] = []

            # Top-level files (Library “Add to chat” flow)
            top_files = body.get("files")
            if isinstance(top_files, list):
                for f in top_files:
                    if isinstance(f, Mapping):
                        candidates.append(f)

            # Last user message files (paperclip flow)
            messages = body.get("chat", {}).get("messages") if isinstance(body.get("chat"), Mapping) else body.get("messages")
            if isinstance(messages, list) and messages:
                last = messages[-1]
                if isinstance(last, Mapping):
                    for key in ("files", "attachments"):
                        vals = last.get(key)
                        if isinstance(vals, list):
                            for f in vals:
                                if isinstance(f, Mapping):
                                    candidates.append(f)
                    # Some Open WebUI versions put file content directly in the message
                    # as `file_ids` with separate collection; handle generically.

            seen: set[str] = set()
            for f in candidates:
                file_id = self._optional_id(f, "id") or self._optional_id(f, "file_id") or self._optional_id(f, "name")
                if not file_id or file_id in seen:
                    continue
                seen.add(file_id)

                # Try to get text content: prefer `content`, `text`, `data`, or `file` fields
                content = None
                for key in ("content", "text", "data", "file_content", "extracted_content"):
                    val = f.get(key)
                    if isinstance(val, str) and val.strip():
                        content = val
                        break
                # Fallback: fetch via Open WebUI file content API when only url/id is present
                if not content or not content.strip():
                    url = self._optional_id(f, "url")
                    fetch_id = file_id
                    if url and "/files/" in url:
                        try:
                            parts = url.split("/files/")[1].split("/")[0].split("?")[0]
                            if parts:
                                fetch_id = parts
                        except Exception:
                            pass
                    token = None
                    if isinstance(__user__, Mapping):
                        token = self._optional_id(__user__, "token") or self._optional_id(__user__, "access_token") or self._optional_id(__user__, "jwt")
                    if not token:
                        # Fallback to Valve-stored Open WebUI API key (for Library/paperclip where __user__ has no token)
                        valve_key = self.valves.open_webui_api_key.strip() if hasattr(self.valves, "open_webui_api_key") else ""
                        if valve_key:
                            token = valve_key
                    # Debug: log what we are about to fetch
                    print(
                        f"file_index_filter: fetch attempt file_id={file_id} fetch_id={fetch_id} has_url={bool(url)} token_present={bool(token)} keys={list(f.keys())}",
                        file=sys.stderr,
                    )
                    try:
                        headers = {}
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                        # Prefer the url field if it's already a full URL, else construct
                        if url and url.startswith("http"):
                            fetch_url = url
                        elif url and url.startswith("/"):
                            fetch_url = f"{self.valves.open_webui_url.rstrip('/')}{url}"
                        else:
                            fetch_url = f"{self.valves.open_webui_url.rstrip('/')}/api/v1/files/{fetch_id}/content"
                        # Use a longer timeout for file content (docx/pdf can be large)
                        with httpx.Client(timeout=max(self.valves.timeout_seconds, 10.0)) as client:
                            resp = client.get(fetch_url, headers=headers)
                            print(
                                f"file_index_filter: fetch {fetch_url} -> {resp.status_code} len={len(resp.content) if resp.content else 0}",
                                file=sys.stderr,
                            )
                            if resp.status_code == 200:
                                body_bytes = resp.content
                                if body_bytes[:2] == b"PK":  # docx is zip
                                    try:
                                        import io
                                        import zipfile
                                        import xml.etree.ElementTree as ET

                                        with zipfile.ZipFile(io.BytesIO(body_bytes)) as z:
                                            xml_content = z.read("word/document.xml")
                                            root = ET.fromstring(xml_content)
                                            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                                            texts = [n.text for n in root.findall(".//w:t", ns) if n.text]
                                            content = " ".join(texts)
                                            print(f"file_index_filter: docx extracted {len(content)} chars", file=sys.stderr)
                                    except Exception as e:
                                        print(f"file_index_filter docx extract fail {e}", file=sys.stderr)
                                        content = body_bytes.decode("utf-8", errors="ignore")
                                else:
                                    content = body_bytes.decode("utf-8", errors="ignore")
                                    print(f"file_index_filter: text decoded {len(content)} chars", file=sys.stderr)
                    except Exception as exc:
                        print(f"file_index_filter fetch fail-open {exc}", file=sys.stderr)
                        content = None
                if not content or not content.strip():
                    continue
                if len(content) > 500_000:
                    content = content[:500_000]

                self._signed_post(
                    "/v1/files/materialize",
                    {
                        "native_user_id": user_id,
                        "native_file_id": file_id,
                        "content": content,
                        "native_chat_id": self._optional_id(body.get("chat"), "id") or self._optional_id(body, "chat_id"),
                    },
                )
        except Exception as exc:  # noqa: BLE001
            print(f"file_index_filter inlet fail-open {exc}", file=sys.stderr)
        return body

    def outlet(self, body: dict[str, Any], __user__: dict | None = None) -> dict[str, Any]:
        """No-op outlet; indexing is done on inlet."""
        return body
