"""
title: Assistant Core Documents & Spreadsheets
version: 0.1.0
requirements: httpx
"""

import base64
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

        assistant_core_url: str = Field(
            default="http://assistant-core:8080",
            description="Assistant Core API backend URL",
        )
        hmac_secret: str = Field(
            default="development-hmac-secret-change-me",
            description="Shared HMAC secret matching ASSISTANT_HMAC_SECRET",
            json_schema_extra={"input": {"type": "password"}},
        )
        open_webui_url: str = Field(
            default="http://localhost:8080",
            description="Open WebUI backend URL for native file uploads",
        )
        open_webui_api_key: str = Field(
            default="",
            description="Open WebUI API Key (from Settings -> Account -> API Keys) for native file uploads",
            json_schema_extra={"input": {"type": "password"}},
        )
        public_assistant_url: str = Field(
            default="",
            description="Optional public Assistant Core URL (e.g. https://mem.app.amhl.ovh) to replace internal docker URLs in links",
        )
        timeout_seconds: float = Field(default=15.0, ge=0.5, le=60.0)

    def __init__(self) -> None:
        """Initialise the Tool with administrator-configured valves."""
        self.valves = self.Valves()

    @staticmethod
    def _optional_id(container: object, key: str) -> str | None:
        if not isinstance(container, Mapping):
            return None
        identifier = container.get(key)
        return None if identifier is None else str(identifier)

    def _extract_openwebui_token(self, user: dict | None, request: object | None) -> str:
        """Extract user or admin token to authorize Open WebUI file uploads."""
        if self.valves.open_webui_api_key:
            return self.valves.open_webui_api_key.strip()

        if isinstance(user, Mapping):
            for k in ("token", "api_key", "jwt"):
                if user.get(k):
                    return str(user[k]).strip()

        if request is not None:
            headers = getattr(request, "headers", None)
            if isinstance(headers, Mapping):
                auth = headers.get("authorization") or headers.get("Authorization")
                if auth and str(auth).startswith("Bearer "):
                    return str(auth)[7:].strip()
            cookies = getattr(request, "cookies", None)
            if isinstance(cookies, Mapping) and cookies.get("token"):
                return str(cookies["token"]).strip()

        return ""

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

    async def _upload_to_open_webui(
        self,
        filename: str,
        file_bytes: bytes,
        mime_type: str,
        user: dict | None,
        request: object | None = None,
    ) -> tuple[str | None, str | None]:
        """Upload generated binary directly to Open WebUI's native /api/v1/files/ store."""
        token = self._extract_openwebui_token(user, request)
        if not token:
            return None, "No Open WebUI API key found in Tool Valves (open_webui_api_key is empty)"

        configured = self.valves.open_webui_url.rstrip("/") if self.valves.open_webui_url else ""
        candidates = [configured] if configured else []
        for fallback in [
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://open-webui:8080",
        ]:
            if fallback and fallback not in candidates:
                candidates.append(fallback)

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        files = {"file": (filename, file_bytes, mime_type)}
        last_error = "Could not reach Open WebUI file API"

        for base_url in candidates:
            for path in ("/api/v1/files/", "/api/v1/files"):
                target = f"{base_url}{path}"
                try:
                    async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                        res = await client.post(target, headers=headers, files=files)
                        if res.status_code in (200, 201):
                            data = res.json()
                            file_id = data.get("id") or (
                                data.get("file", {}).get("id") if isinstance(data.get("file"), dict) else None
                            )
                            if file_id:
                                return f"/api/v1/files/{file_id}/content", None
                        else:
                            last_error = f"{target} returned HTTP {res.status_code}"
                except Exception as exc:  # noqa: BLE001
                    last_error = f"{target} connection error: {exc}"

        return None, last_error

    async def _format_result(
        self,
        title: str,
        ext: str,
        icon: str,
        res: dict[str, Any],
        user: dict | None = None,
        request: object | None = None,
    ) -> str:
        """Format a clean markdown response with native Open WebUI download links."""
        art_id = res.get("id")
        v_num = res.get("current_version_num", 1)
        mime_type = res.get("mime_type") or "application/octet-stream"
        b64 = res.get("base64_data")
        filename = f"{title}.{ext}" if not title.endswith(f".{ext}") else title

        download_link = None
        upload_err = None
        if b64:
            try:
                b64_padded = b64 + "=" * (-len(b64) % 4)
                raw_bytes = base64.b64decode(b64_padded)
                openwebui_link, upload_err = await self._upload_to_open_webui(
                    filename, raw_bytes, mime_type, user, request
                )
                if openwebui_link:
                    download_link = openwebui_link
            except Exception as exc:  # noqa: BLE001
                upload_err = str(exc)

        if not download_link:
            raw_dl = res.get("download_url") or f"/v1/artifacts/{art_id}/download"
            if self.valves.public_assistant_url:
                download_link = f"{self.valves.public_assistant_url.rstrip('/')}/v1/artifacts/{art_id}/download"
            else:
                download_link = raw_dl

        lines = [
            f"{icon} **{ext.upper()} Created**: `{filename}` (v{v_num})",
            "",
            f"- **Download**: [⬇️ Download `{filename}`]({download_link})",
            f"- **Artifact ID**: `{art_id}`",
        ]
        if not download_link.startswith("/api/v1/files/") and upload_err:
            lines.append("")
            lines.append(f"*(Open WebUI File Store note: {upload_err})*")

        return "\n".join(lines)

    async def create_spreadsheet(
        self,
        title: str,
        sheets_json: str,
        __user__: dict | None = None,
        __request__: object | None = None,
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

            return await self._format_result(
                title=title.strip(), ext="xlsx", icon="📊", res=res, user=__user__, request=__request__
            )
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
        __request__: object | None = None,
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

            return await self._format_result(
                title=title.strip(), ext="docx", icon="📄", res=res, user=__user__, request=__request__
            )
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
        __request__: object | None = None,
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

            return await self._format_result(
                title=title.strip(), ext="pptx", icon="📽️", res=res, user=__user__, request=__request__
            )
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, KeyError, AttributeError):
                detail = exc.response.text or str(exc)
            return f"Failed to generate presentation '{title}': {detail}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to generate presentation '{title}': {exc}"
