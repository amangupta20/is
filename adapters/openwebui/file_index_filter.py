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

            candidates: list[Mapping[str, object]] = []

            top_files = body.get("files")
            if isinstance(top_files, list):
                for f in top_files:
                    if isinstance(f, Mapping):
                        candidates.append(f)

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

            seen: set[str] = set()
            for f in candidates:
                file_id = self._optional_id(f, "id") or self._optional_id(f, "file_id") or self._optional_id(f, "name")
                if not file_id or file_id in seen:
                    continue
                seen.add(file_id)

                content = None
                for key in ("content", "text", "data", "file_content", "extracted_content"):
                    val = f.get(key)
                    if isinstance(val, str) and val.strip():
                        content = val
                        break
                if not content or not content.strip():
                    nested = f.get("file")
                    if isinstance(nested, Mapping):
                        for key in ("content", "text", "data"):
                            val = nested.get(key)
                            if isinstance(val, str) and val.strip():
                                content = val
                                break
                        if not content:
                            b64 = nested.get("content")
                            if isinstance(b64, str) and len(b64) > 100 and b64.strip():
                                try:
                                    import base64

                                    decoded = base64.b64decode(b64).decode("utf-8", errors="ignore")
                                    if decoded.strip():
                                        content = decoded
                                except Exception:
                                    pass
                        if not content or not content.strip():
                            for key in ("data", "content"):
                                val = nested.get(key)
                                if isinstance(val, str) and len(val) > 100:
                                    try:
                                        import base64
                                        import io

                                        raw = base64.b64decode(val)
                                        if raw[:2] == b"%PDF":
                                            try:
                                                from pypdf import PdfReader

                                                reader = PdfReader(io.BytesIO(raw))
                                                texts = [page.extract_text() or "" for page in reader.pages]
                                                content = "\n".join(t for t in texts if t.strip())
                                            except Exception:
                                                content = raw.decode("utf-8", errors="ignore")
                                        elif raw[:2] == b"PK":
                                            import zipfile
                                            import xml.etree.ElementTree as ET

                                            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                                                xml_content = z.read("word/document.xml")
                                                root = ET.fromstring(xml_content)
                                                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                                                texts = [n.text for n in root.findall(".//w:t", ns) if n.text]
                                                content = " ".join(texts)
                                    except Exception:
                                        pass
                                    if content and content.strip():
                                        break
                if not content or not content.strip():
                    print(
                        f"file_index_filter: skip file_id={file_id} no inline content keys={list(f.keys())}",
                        file=sys.stderr,
                    )
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
