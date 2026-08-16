"""
title: Assistant Core Documents & Spreadsheets
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
    """Standalone tool for generating styled Excel spreadsheets, Word documents, and PowerPoint decks."""

    _UNAVAILABLE = "Assistant Core Document Service is currently unavailable."

    class Valves(BaseModel):
        """Administrator-managed connection settings."""

        assistant_core_url: str = Field(default="http://assistant-core:8080")
        hmac_secret: str = Field(
            default="development-hmac-secret-change-me",
            json_schema_extra={"input": {"type": "password"}},
        )
        timeout_seconds: float = Field(default=8.0, ge=0.5, le=30.0)

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

    def _format_result(self, title: str, ext: str, icon: str, res: dict[str, Any]) -> str:
        """Format a rich markdown response with direct in-chat download capabilities."""
        art_id = res.get("id")
        v_num = res.get("current_version_num", 1)
        mime_type = res.get("mime_type") or "application/octet-stream"
        b64 = res.get("base64_data")
        filename = f"{title}.{ext}" if not title.endswith(f".{ext}") else title

        lines = [
            f"{icon} **{ext.upper()} Created**: `{filename}` (v{v_num})",
            "",
        ]

        if b64:
            data_uri = f"data:{mime_type};base64,{b64}"
            lines.append(f"- **Direct Download**: [⬇️ Click to Download `{filename}`]({data_uri})")

        dl_url = res.get("download_url")
        if dl_url:
            lines.append(f"- **Server URL**: [🔗 `{dl_url}`]({dl_url})")

        lines.append(f"- **Artifact ID**: `{art_id}`")
        lines.append("")
        lines.append("*The download link works directly inside this browser window with no VPN or internal network access required.*")
        return "\n".join(lines)

    async def create_spreadsheet(
        self,
        title: str,
        sheets_json: str,
        __user__: dict | None = None,
    ) -> str:
        """Create a styled multi-tab Excel spreadsheet (.xlsx) with auto-widths, formulas, and number formats.
        sheets_json must be a JSON array of sheets: [{"name": "Sheet1", "headers": ["A", "B"], "rows": [[1, 2]], "column_types": ["text", "currency"], "totals_row": true}]
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            sheets_data = json.loads(sheets_json) if isinstance(sheets_json, str) else sheets_json
            payload = {
                "native_user_id": native_user_id,
                "title": title.strip(),
                "artifact_type": "xlsx",
                "workbook_spec": {
                    "title": title.strip(),
                    "sheets": sheets_data if isinstance(sheets_data, list) else [sheets_data],
                },
                "change_summary": "Generated spreadsheet",
            }
            res = await self._signed_json_post("/v1/artifacts/create", payload)
            if not isinstance(res, dict):
                return self._UNAVAILABLE

            return self._format_result(title=title.strip(), ext="xlsx", icon="📊", res=res)
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, KeyError, AttributeError):
                detail = exc.response.text or str(exc)
            return f"Failed to generate spreadsheet '{title}': {detail}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to generate spreadsheet '{title}': {exc}"

    async def create_document(
        self,
        title: str,
        sections_json: str,
        subtitle: str = "",
        __user__: dict | None = None,
    ) -> str:
        """Create a styled Word document (.docx) with typography, callouts, and data tables.
        sections_json must be a JSON array of sections: [{"heading": "Section 1", "level": 1, "paragraphs": ["text..."], "bullets": ["item..."], "callout": "note..."}]
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            sections_data = json.loads(sections_json) if isinstance(sections_json, str) else sections_json
            payload = {
                "native_user_id": native_user_id,
                "title": title.strip(),
                "artifact_type": "docx",
                "document_spec": {
                    "title": title.strip(),
                    "subtitle": subtitle.strip() or None,
                    "sections": sections_data if isinstance(sections_data, list) else [sections_data],
                },
                "change_summary": "Generated document",
            }
            res = await self._signed_json_post("/v1/artifacts/create", payload)
            if not isinstance(res, dict):
                return self._UNAVAILABLE

            return self._format_result(title=title.strip(), ext="docx", icon="📄", res=res)
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, KeyError, AttributeError):
                detail = exc.response.text or str(exc)
            return f"Failed to generate document '{title}': {detail}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to generate document '{title}': {exc}"

    async def create_presentation(
        self,
        title: str,
        slides_json: str,
        subtitle: str = "",
        __user__: dict | None = None,
    ) -> str:
        """Create a 16:9 widescreen PowerPoint presentation (.pptx) deck.
        slides_json must be a JSON array of slides: [{"title": "Slide Title", "layout": "bullets", "bullets": ["..."]}]
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            slides_data = json.loads(slides_json) if isinstance(slides_json, str) else slides_json
            payload = {
                "native_user_id": native_user_id,
                "title": title.strip(),
                "artifact_type": "pptx",
                "presentation_spec": {
                    "title": title.strip(),
                    "subtitle": subtitle.strip() or None,
                    "slides": slides_data if isinstance(slides_data, list) else [slides_data],
                },
                "change_summary": "Generated presentation",
            }
            res = await self._signed_json_post("/v1/artifacts/create", payload)
            if not isinstance(res, dict):
                return self._UNAVAILABLE

            return self._format_result(title=title.strip(), ext="pptx", icon="📽️", res=res)
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, KeyError, AttributeError):
                detail = exc.response.text or str(exc)
            return f"Failed to generate presentation '{title}': {detail}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to generate presentation '{title}': {exc}"
