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

    @classmethod
    def _extract_scope_ids(
        cls, metadata: Mapping[object, object] | None
    ) -> tuple[str | None, str | None]:
        folder_id: str | None = None
        project_id: str | None = None
        if isinstance(metadata, Mapping):
            folder_id = cls._optional_id(metadata, "folder_id")
            project_id = cls._optional_id(metadata, "project_id")
            chat = metadata.get("chat")
            if folder_id is None and isinstance(chat, Mapping):
                folder_id = cls._optional_id(chat, "folder_id")
            if project_id is None and isinstance(chat, Mapping):
                project_id = cls._optional_id(chat, "project_id")
        return folder_id, project_id

    def _extract_openwebui_token(self, user: dict | None, request: object | None) -> str:
        """Extract user session token first so files are owned by the active user, falling back to admin key."""
        if request is not None:
            headers = getattr(request, "headers", None)
            if isinstance(headers, Mapping):
                auth = headers.get("authorization") or headers.get("Authorization")
                if auth and str(auth).startswith("Bearer "):
                    return str(auth)[7:].strip()
            cookies = getattr(request, "cookies", None)
            if isinstance(cookies, Mapping) and cookies.get("token"):
                return str(cookies["token"]).strip()

        if isinstance(user, Mapping):
            for k in ("token", "api_key", "jwt"):
                if user.get(k):
                    return str(user[k]).strip()

        if self.valves.open_webui_api_key:
            return self.valves.open_webui_api_key.strip()

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
                                data.get("file", {}).get("id")
                                if isinstance(data.get("file"), dict)
                                else None
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
                download_link = (
                    f"{self.valves.public_assistant_url.rstrip('/')}/v1/artifacts/{art_id}/download"
                )
            else:
                download_link = raw_dl

        lines = [
            f"{icon} **{ext.upper()} Created**: `{filename}` (v{v_num})",
            "",
            f"- **Download**: [⬇️ Download `{filename}`]({download_link})",
            f"- **Artifact ID**: `{art_id}`",
        ]
        if res.get("onlyoffice_url"):
            lines.append(f"- **OnlyOffice**: [Open Document Editor]({res['onlyoffice_url']})")
        if not download_link.startswith("/api/v1/files/") and upload_err:
            lines.append("")
            lines.append(f"*(Open WebUI File Store note: {upload_err})*")

        return "\n".join(lines)

    async def create_spreadsheet(
        self,
        title: str,
        sheets_json: str,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __request__: object | None = None,
    ) -> str:
        """Create a styled multi-tab Excel spreadsheet (.xlsx) with auto-widths, formulas, and number formats.
        Use whenever the user asks for a spreadsheet, table export, budget, tracker, or any downloadable Excel file.
        sheets_json must be a JSON array of sheets: [{"name": "Sheet1", "headers": ["A", "B"], "rows": [[1, 2]], "column_types": ["text", "currency"], "totals_row": true}]
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            folder_id, project_id = self._extract_scope_ids(__metadata__)
            sheets_data = json.loads(sheets_json) if isinstance(sheets_json, str) else sheets_json
            payload = {
                "native_user_id": native_user_id,
                "title": title.strip(),
                "artifact_type": "xlsx",
                "native_project_id": project_id,
                "native_folder_id": folder_id,
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
                title=title.strip(),
                ext="xlsx",
                icon="📊",
                res=res,
                user=__user__,
                request=__request__,
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
        theme: str = "slate",
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __request__: object | None = None,
    ) -> str:
        """Create a styled Word document (.docx) with typography, callouts, images, and data tables.
        Use whenever the user asks for a Word document or downloadable report/letter in .docx form.
        sections_json: JSON array of sections: [{"heading": "Sec 1", "level": 1, "paragraphs": ["..."], "bullets": ["..."], "callout": "...", "image_url": "https://...", "image_caption": "Figure 1", "table": {"headers": ["A", "B"], "rows": [["1", "2"]]}}]
        theme: Color theme preset ('slate', 'navy', 'emerald', 'crimson', 'dark').
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            folder_id, project_id = self._extract_scope_ids(__metadata__)
            sections_data = (
                json.loads(sections_json) if isinstance(sections_json, str) else sections_json
            )
            payload = {
                "native_user_id": native_user_id,
                "title": title.strip(),
                "artifact_type": "docx",
                "native_project_id": project_id,
                "native_folder_id": folder_id,
                "document_spec": {
                    "title": title.strip(),
                    "subtitle": subtitle.strip() or None,
                    "theme": theme
                    if theme in ("slate", "navy", "emerald", "crimson", "dark")
                    else "slate",
                    "sections": sections_data
                    if isinstance(sections_data, list)
                    else [sections_data],
                },
                "change_summary": "Generated document",
            }
            res = await self._signed_json_post("/v1/artifacts/create", payload)
            if not isinstance(res, dict):
                return self._UNAVAILABLE

            return await self._format_result(
                title=title.strip(),
                ext="docx",
                icon="📄",
                res=res,
                user=__user__,
                request=__request__,
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
        theme: str = "slate",
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __request__: object | None = None,
    ) -> str:
        """Create a 16:9 widescreen PowerPoint presentation (.pptx) deck with images, native charts, and milestone timelines.
        Use whenever the user asks for slides, a deck, or a downloadable presentation.
        slides_json: JSON array of slides. Layout options:
          - 'bullets': {"title": "...", "bullets": ["..."]}
          - 'cards': {"title": "...", "cards": [{"title": "KPI", "value": "$1.2M", "description": "..."}]}
          - 'comparison': {"title": "...", "left_column": ["..."], "right_column": ["..."]}
          - 'quote': {"title": "...", "quote": "...", "author": "..."}
          - 'image_right' / 'image_left': {"title": "...", "bullets": ["..."], "image_url": "https://...", "image_caption": "..."}
          - 'full_image': {"title": "...", "image_url": "https://...", "image_caption": "..."}
          - 'chart': {"title": "...", "chart_type": "column"|"bar"|"line"|"pie", "chart_categories": ["Q1", "Q2"], "chart_series": [{"name": "Sales", "values": [10, 20]}]}
          - 'timeline': {"title": "...", "timeline_steps": [{"step": "Phase 1", "title": "Discovery", "description": "..."}]}
        theme: Color theme preset ('slate', 'navy', 'emerald', 'crimson', 'dark').
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            folder_id, project_id = self._extract_scope_ids(__metadata__)
            slides_data = json.loads(slides_json) if isinstance(slides_json, str) else slides_json
            payload = {
                "native_user_id": native_user_id,
                "title": title.strip(),
                "artifact_type": "pptx",
                "native_project_id": project_id,
                "native_folder_id": folder_id,
                "presentation_spec": {
                    "title": title.strip(),
                    "subtitle": subtitle.strip() or None,
                    "theme": theme
                    if theme in ("slate", "navy", "emerald", "crimson", "dark")
                    else "slate",
                    "slides": slides_data if isinstance(slides_data, list) else [slides_data],
                },
                "change_summary": "Generated presentation",
            }
            res = await self._signed_json_post("/v1/artifacts/create", payload)
            if not isinstance(res, dict):
                return self._UNAVAILABLE

            return await self._format_result(
                title=title.strip(),
                ext="pptx",
                icon="📽️",
                res=res,
                user=__user__,
                request=__request__,
            )
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, KeyError, AttributeError):
                detail = exc.response.text or str(exc)
            return f"Failed to generate presentation '{title}': {detail}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to generate presentation '{title}': {exc}"

    async def create_pdf(
        self,
        title: str,
        sections_json: str,
        subtitle: str = "",
        theme: str = "slate",
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __request__: object | None = None,
    ) -> str:
        """Create a styled publication-ready PDF document (.pdf) with typography, callouts, images, and data tables.
        Use whenever the user asks for a polished downloadable PDF (reports, notes, one-pagers) without needing Word format.
        sections_json: JSON array of sections: [{"heading": "Sec 1", "level": 1, "paragraphs": ["..."], "bullets": ["..."], "callout": "...", "image_url": "https://...", "image_caption": "Figure 1", "table": {"headers": ["A", "B"], "rows": [["1", "2"]]}}]
        theme: Color theme preset ('slate', 'navy', 'emerald', 'crimson', 'dark').
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            folder_id, project_id = self._extract_scope_ids(__metadata__)
            sections_data = (
                json.loads(sections_json) if isinstance(sections_json, str) else sections_json
            )
            payload = {
                "native_user_id": native_user_id,
                "title": title.strip(),
                "artifact_type": "pdf",
                "native_project_id": project_id,
                "native_folder_id": folder_id,
                "document_spec": {
                    "title": title.strip(),
                    "subtitle": subtitle.strip() or None,
                    "theme": theme
                    if theme in ("slate", "navy", "emerald", "crimson", "dark")
                    else "slate",
                    "sections": sections_data
                    if isinstance(sections_data, list)
                    else [sections_data],
                },
                "change_summary": "Generated PDF document",
            }
            res = await self._signed_json_post("/v1/artifacts/create", payload)
            if not isinstance(res, dict):
                return self._UNAVAILABLE

            return await self._format_result(
                title=title.strip(),
                ext="pdf",
                icon="📕",
                res=res,
                user=__user__,
                request=__request__,
            )
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, KeyError, AttributeError):
                detail = exc.response.text or str(exc)
            return f"Failed to generate PDF '{title}': {detail}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to generate PDF '{title}': {exc}"

    async def create_typst_document(
        self,
        title: str,
        markup: str,
        change_summary: str = "Generated Typst document",
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __request__: object | None = None,
        __event_emitter__: Any = None,
    ) -> str:
        """Create a publication-quality PDF document (.pdf) compiled directly from Typst markup.
        Use for precise, complex PDF layouts (papers, specs, formatted documents) when full layout control is needed and plain sections_json is insufficient.
        markup: Typst markup source code (e.g. '= Title\\n\\n== Heading\\nParagraph text...').
        change_summary: Summary of this generation or revision.
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            folder_id, project_id = self._extract_scope_ids(__metadata__)
            payload = {
                "native_user_id": native_user_id,
                "title": title.strip(),
                "artifact_type": "typst",
                "native_project_id": project_id,
                "native_folder_id": folder_id,
                "raw_content": markup,
                "change_summary": change_summary.strip()
                if change_summary
                else "Generated Typst document",
            }
            res = await self._signed_json_post("/v1/artifacts/create", payload)
            if not isinstance(res, dict):
                return self._UNAVAILABLE

            return await self._format_result(
                title=title.strip(),
                ext="pdf",
                icon="📐",
                res=res,
                user=__user__,
                request=__request__,
            )
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, KeyError, AttributeError):
                detail = exc.response.text or str(exc)
            return f"Failed to generate Typst document '{title}': {detail}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to generate Typst document '{title}': {exc}"

    async def create_resume(
        self,
        title: str,
        resume_json: str,
        change_summary: str = "Generated resume",
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __request__: object | None = None,
        __event_emitter__: Any = None,
    ) -> str:
        """Create a professional, publication-grade PDF resume (.pdf) compiled via Typst from structured JSON.
        Use whenever the user asks to build, format, or export a resume or CV.
        resume_json: JSON string representing ResumeSpec:
          - 'name': Full name (str)
          - 'title': Professional subtitle/title (optional str)
          - 'email', 'phone', 'location', 'website', 'github', 'linkedin': Contact info (optional str)
          - 'summary': Summary profile (optional str)
          - 'experience': list of [{"company": "...", "position": "...", "location": "...", "start_date": "...", "end_date": "...", "highlights": ["..."], "technologies": ["..."]}]
          - 'education': list of [{"institution": "...", "degree": "...", "field_of_study": "...", "start_date": "...", "end_date": "...", "location": "...", "highlights": ["..."]}]
          - 'skills': list of [{"name": "Category", "skills": ["Skill1", "Skill2"]}]
          - 'projects': list of [{"name": "...", "description": "...", "url": "...", "highlights": ["..."], "technologies": ["..."]}]
        change_summary: Summary of this generation or revision.
        """
        try:
            native_user_id = self._optional_id(__user__, "id") or "unknown"
            folder_id, project_id = self._extract_scope_ids(__metadata__)
            resume_data = json.loads(resume_json) if isinstance(resume_json, str) else resume_json
            payload = {
                "native_user_id": native_user_id,
                "title": title.strip(),
                "artifact_type": "resume",
                "native_project_id": project_id,
                "native_folder_id": folder_id,
                "resume_spec": resume_data,
                "change_summary": change_summary.strip() if change_summary else "Generated resume",
            }
            res = await self._signed_json_post("/v1/artifacts/create", payload)
            if not isinstance(res, dict):
                return self._UNAVAILABLE

            return await self._format_result(
                title=title.strip(),
                ext="pdf",
                icon="💼",
                res=res,
                user=__user__,
                request=__request__,
            )
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, KeyError, AttributeError):
                detail = exc.response.text or str(exc)
            return f"Failed to generate resume '{title}': {detail}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to generate resume '{title}': {exc}"
