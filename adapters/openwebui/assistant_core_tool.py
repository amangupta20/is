"""
title: Assistant Core Tools & Diagnostics
version: 0.2.0
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
    """Explicit tools and observability diagnostics for Assistant Core."""

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

    @staticmethod
    def _media_time_str(start_sec: object, end_sec: object) -> str:
        """Render an ``@ MM:SS[-MM:SS]`` marker from possibly fractional seconds."""
        if isinstance(start_sec, bool) or not isinstance(start_sec, int | float):
            return ""
        start = max(0, int(start_sec))
        end = start
        if isinstance(end_sec, int | float) and not isinstance(end_sec, bool):
            end = max(0, int(end_sec))
        s_min, s_sec = divmod(start, 60)
        e_min, e_sec = divmod(end, 60)
        if end != start:
            return f" @ {s_min:02d}:{s_sec:02d}-{e_min:02d}:{e_sec:02d}"
        return f" @ {s_min:02d}:{s_sec:02d}"

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
    def _context_payload(__user__: dict | None, __metadata__: dict | None) -> dict[str, object]:
        """Build the bounded native identity envelope used by context routes."""
        folder_id = Tools._optional_id(__metadata__, "folder_id")
        project_id = Tools._optional_id(__metadata__, "project_id")
        if folder_id is None and isinstance(__metadata__, Mapping):
            chat = __metadata__.get("chat")
            if isinstance(chat, Mapping):
                folder_id = Tools._optional_id(chat, "folder_id")
        if project_id is None and isinstance(__metadata__, Mapping):
            chat = __metadata__.get("chat")
            if isinstance(chat, Mapping):
                project_id = Tools._optional_id(chat, "project_id")

        return {
            "native_user_id": Tools._optional_id(__user__, "id") or "unknown",
            "native_chat_id": Tools._optional_id(__metadata__, "chat_id"),
            "native_project_id": project_id,
            "native_folder_id": folder_id,
            "native_message_id": Tools._optional_id(__metadata__, "message_id"),
        }

    async def show_loaded_profile(
        self,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Show the personalization profile currently loaded into this chat, with source memory IDs.

        Use when the user asks what you know or remember about them, or to verify
        context before answering a personal question.
        """
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
                if (
                    not isinstance(source, dict)
                    or set(source)
                    != {
                        "source_type",
                        "source_id",
                        "label",
                    }
                    or any(type(source[key]) is not str for key in source)
                ):
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
        include_pasted_files: bool = False,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Search the user's durable memories, past conversations, indexed documents, topic episodes, and processed media.

        ALWAYS call this before stating or assuming anything about the user's
        preferences, history, prior decisions, projects, or uploaded files, and before
        answering questions about earlier discussions. Do not use for general
        knowledge, coding help, or anything already visible in this chat.
        :param query: Natural-language search text (keywords work best).
        :param limit: Maximum results to return (1-10).
        :param include_pasted_files: Also search transient auto-saved pasted-text files,
            e.g. when the user asks about a log or text they pasted earlier.
        """
        try:
            payload = self._context_payload(__user__, __metadata__)
            payload.update(
                {
                    "query": query.strip(),
                    "limit": limit,
                    "include_pasted_files": bool(include_pasted_files),
                }
            )
            response = await self._signed_json_post("/v1/personal-context/search", payload)
            if not isinstance(response, dict) or set(response) != {"mode", "results"}:
                return self._UNAVAILABLE
            results = response["results"]
            mode = response["mode"]
            if mode not in {"lexical", "hybrid"} or type(results) is not list:
                return self._UNAVAILABLE
            lines: list[str] = []
            for result in results:
                if not isinstance(result, dict):
                    return self._UNAVAILABLE
                source_type = result.get("source_type")
                source_id = result.get("source_id")
                category = result.get("category")
                preview = result.get("preview")
                role = result.get("role")
                chat_id = result.get("source_native_chat_id")
                message_id = result.get("source_native_message_id")
                filename = result.get("filename")
                header_path = result.get("header_path")
                chunk_ordinal = result.get("chunk_ordinal")

                if not isinstance(source_id, str) or not isinstance(preview, str):
                    return self._UNAVAILABLE

                if source_type == "memory":
                    label = f"memory/{category}"
                    provenance = ""
                elif source_type == "conversation":
                    label = f"conversation/{role} evidence"
                    provenance = f" (chat {chat_id}, message {message_id})"
                elif source_type == "file":
                    fname = filename or "document"
                    hpath = f" § {header_path}" if header_path else ""
                    label = f"file/{fname}{hpath}"
                    provenance = f" [chunk {chunk_ordinal}]"
                elif source_type == "episode":
                    ep_title = result.get("title") or "Episode"
                    label = f"episode/{category} § {ep_title}"
                    provenance = f" (chat {chat_id})"
                elif source_type == "media":
                    med_title = result.get("title") or "Media"
                    time_str = self._media_time_str(
                        result.get("start_time_seconds"), result.get("end_time_seconds")
                    )
                    label = f"media/{med_title}{time_str}"
                    label_val = result.get("label")
                    provenance = f" ({label_val})" if label_val else ""
                else:
                    return self._UNAVAILABLE

                lines.append(f"- [{label}] {source_id}: {preview}{provenance}")
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
        """Expand one source found by search_personal_context into full bounded content.

        Call this on a result's source_id when its preview is relevant but you need
        the surrounding text, exact wording, evidence quote, or neighboring messages
        before answering.
        :param memory_source_id: The source_id from search_personal_context results.
        """
        try:
            payload = {
                "native_user_id": self._optional_id(__user__, "id") or "unknown",
                "memory_source_id": memory_source_id,
            }
            response = await self._signed_json_post("/v1/personal-context/read", payload)
            if not isinstance(response, dict):
                return self._UNAVAILABLE

            source_type = response.get("source_type")
            source_id = response.get("source_id")
            content = response.get("content")
            category = response.get("category")
            role = response.get("role")
            evidence_quote = response.get("evidence_quote")

            if not isinstance(source_id, str) or not isinstance(content, str):
                return self._UNAVAILABLE

            if source_type == "memory":
                role_line = f"Role: {role}\n" if role is not None else ""
                evidence_line = (
                    f'Evidence: "{evidence_quote}"\n' if evidence_quote is not None else ""
                )
                return (
                    f"Personal memory source {source_id}:\n"
                    f"Content: {content}\n"
                    f"Category: {category}\n"
                    f"{role_line}"
                    f"{evidence_line}"
                    f"Source chat: {response.get('source_native_chat_id')}\n"
                    f"Source message: {response.get('source_native_message_id')}\n"
                    "Neighboring context: none\n"
                    "Full-source expansion available: "
                    f"{'yes' if response.get('full_source_available') else 'no'}"
                )
            if source_type == "conversation":
                neighbor_lines: list[str] = []
                for neighbor in response.get("neighbors", []):
                    if isinstance(neighbor, dict):
                        neighbor_lines.append(
                            f"- {neighbor.get('role')} ({neighbor.get('source_native_message_id')}): "
                            f"{neighbor.get('content')}"
                        )
                neighbor_text = "\n".join(neighbor_lines) if neighbor_lines else "none"
                return (
                    f"Personal conversation source {source_id}:\n"
                    f"Content: {content}\n"
                    f"Category: {category}\n"
                    f"Role: {role}\n"
                    f"Source chat: {response.get('source_native_chat_id')}\n"
                    f"Source message: {response.get('source_native_message_id')}\n"
                    f"Neighboring context: {neighbor_text}\n"
                    "Full-source expansion available: "
                    f"{'yes' if response.get('full_source_available') else 'no'}"
                )
            if source_type == "file":
                fname = response.get("filename") or "document"
                hpath = response.get("header_path") or "none"
                ordinal = response.get("chunk_ordinal", 0)
                prev = response.get("previous_chunk") or "none"
                nxt = response.get("next_chunk") or "none"
                return (
                    f"Personal file source {source_id}:\n"
                    f"File: {fname}\n"
                    f"Section: {hpath} (chunk {ordinal})\n"
                    f"Content:\n{content}\n\n"
                    f"Previous chunk:\n{prev}\n\n"
                    f"Next chunk:\n{nxt}"
                )
            if source_type == "episode":
                title = response.get("title") or "Topic Episode"
                turn_count = response.get("turn_count", 1)
                return (
                    f"Topic Episode source {source_id}:\n"
                    f"Title: {title}\n"
                    f"Category: {category}\n"
                    f"Turns Compiled: {turn_count}\n"
                    f"Source chat: {response.get('source_native_chat_id')}\n\n"
                    f"{content}"
                )
            if source_type == "media":
                title = response.get("title") or "Media"
                url = response.get("url") or ""
                time_str = self._media_time_str(
                    response.get("start_time_seconds"), response.get("end_time_seconds")
                )
                return (
                    f"Media source {source_id}:\n"
                    f"Media: media/{title}{time_str}\n"
                    f"URL: {url}\n"
                    f"Category: {category}\n\n"
                    f"{content}"
                )
            return self._UNAVAILABLE
        except Exception:  # noqa: BLE001 - optional memory read must fail open.
            return self._UNAVAILABLE

    async def assistant_status(
        self,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Check Assistant Core availability and aggregate queue health.

        Diagnostic use only: when the user reports memory/recall not working, or you
        suspect the companion is degraded.
        """
        native_user_id = self._optional_id(__user__, "id") or "unknown"
        payload = {
            "native_user_id": native_user_id,
            "native_chat_id": self._optional_id(__metadata__, "chat_id"),
            "native_message_id": self._optional_id(__metadata__, "message_id"),
        }

        try:
            path = "/v1/status"
            status_payload = await self._signed_json_post(path, payload)
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
            return f"Assistant Core: ok; queued jobs: {queued_jobs}; dead jobs: {dead_jobs}."
        except Exception:  # noqa: BLE001 - optional status must fail closed to unavailable.
            return self._UNAVAILABLE

    async def show_recent_conversations(
        self,
        limit: int = 5,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """List the most recent indexed conversations (references only, no content).

        Use to orient when the user vaguely references "that chat about..." and a
        keyword search is not landing; follow up with search_personal_context.
        :param limit: How many recent conversations to show (1-10).
        """
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
        """Show aggregate conversation index counters without content.

        Diagnostic use only, e.g. when recall quality seems off or the user asks how
        much of their history is indexed.
        """
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
                f"Conversation Index stats: segments {data['total_segments']} "
                f"(embedded {data['embedded_segments']}, lexical {data['lexical_segments']}), "
                f"references {data['total_references']} "
                f"(active {data['active_references']}, tombstoned {data['tombstoned_references']}), "
                f"jobs queued {data['queued_jobs']} dead {data['dead_jobs']}, "
                f"last indexed {last_str}."
            )
        except Exception:  # noqa: BLE001
            return self._UNAVAILABLE

    async def show_file_index_status(
        self,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Show aggregate file indexing counters, active documents, and recent files.

        Diagnostic use only; for actual document content use search_personal_context
        or read_full_document.
        """
        native_user_id = self._optional_id(__user__, "id") or "unknown"
        payload = {
            "native_user_id": native_user_id,
            "native_chat_id": self._optional_id(__metadata__, "chat_id"),
            "native_message_id": self._optional_id(__metadata__, "message_id"),
        }
        try:
            stats = await self._signed_json_post("/v1/inspection/files/stats", payload)
            if not isinstance(stats, dict):
                return self._UNAVAILABLE

            recent_payload = {
                "native_user_id": native_user_id,
                "limit": 5,
            }
            recent = await self._signed_json_post("/v1/inspection/files/recent", recent_payload)
            files_list = recent.get("files", []) if isinstance(recent, dict) else []

            lines = [
                f"📁 File Index Stats: {stats.get('total_files', 0)} files ({stats.get('active_files', 0)} active, {stats.get('tombstoned_files', 0)} deleted)",
                f"📊 Segments: {stats.get('total_segments', 0)} total ({stats.get('embedded_segments', 0)} embedded, {stats.get('reused_segments', 0)} deduplicated)",
                f"⚙️ Background Queue: {stats.get('queued_jobs', 0)} queued, {stats.get('dead_jobs', 0)} dead",
            ]
            if files_list:
                lines.append("\nRecent files:")
                for f in files_list:
                    if isinstance(f, dict):
                        lines.append(
                            f"- {f.get('filename')} ({f.get('chunk_count')} chunks, status: {f.get('status')})"
                        )
            else:
                lines.append("\nNo files indexed yet.")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return self._UNAVAILABLE

    async def show_failed_indexing_jobs(
        self,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Show dead/failed background indexing jobs with error codes and attempt counts.

        Diagnostic use only: when files or conversations are not being remembered,
        this reveals whether background indexing is failing.
        """
        native_user_id = self._optional_id(__user__, "id") or "unknown"
        payload = {
            "native_user_id": native_user_id,
            "native_chat_id": self._optional_id(__metadata__, "chat_id"),
            "native_message_id": self._optional_id(__metadata__, "message_id"),
        }
        try:
            data = await self._signed_json_post("/v1/inspection/jobs/dead", payload)
            if not isinstance(data, dict) or "dead_jobs" not in data:
                return self._UNAVAILABLE
            dead_jobs = data["dead_jobs"]
            if not isinstance(dead_jobs, list):
                return self._UNAVAILABLE
            if not dead_jobs:
                return "✅ No failed/dead background jobs. All indexing jobs have completed successfully."
            lines = [f"⚠️ Found {len(dead_jobs)} failed background jobs:"]
            for job in dead_jobs:
                if isinstance(job, dict):
                    lines.append(
                        f"- Job {job.get('job_id')[:8]}… [{job.get('kind')}]: "
                        f"attempts={job.get('attempts')}, error={job.get('last_error') or 'unknown'}"
                    )
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return self._UNAVAILABLE

    async def read_full_document(
        self,
        file_id_or_name: str,
        __user__: dict | None = None,
    ) -> str:
        """Fetch and reconstruct the complete text of an indexed document by file id or exact filename.

        Use for files from EARLIER chats or uploads not attached to this one. For
        files already attached to the current chat, prefer the native file tools.
        :param file_id_or_name: The native file id, or the exact filename (e.g. "specs.md").
        """
        try:
            payload = {
                "native_user_id": self._optional_id(__user__, "id") or "unknown",
                "file_id_or_name": file_id_or_name.strip(),
            }
            response = await self._signed_json_post("/v1/personal-context/file-content", payload)
            if not isinstance(response, dict):
                return self._UNAVAILABLE

            filename = response.get("filename") or "document"
            file_id = response.get("native_file_id") or file_id_or_name
            total_chunks = response.get("total_chunks", 0)
            total_chars = response.get("total_characters", 0)
            content = response.get("content", "")

            if not isinstance(content, str) or not content.strip():
                return f"Document '{file_id_or_name}' has no extracted content."

            return (
                f"📄 Full Document: {filename} (ID: {file_id})\n"
                f"📊 Chunks: {total_chunks} | Length: {total_chars} characters\n"
                f"MIME type: {response.get('mime_type') or 'unknown'}\n\n"
                f"---\n{content}\n---"
            )
        except Exception:  # noqa: BLE001
            return f"Document '{file_id_or_name}' could not be found or is unavailable."

    async def process_media_url(
        self,
        url: str,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Queue a YouTube/media URL for deep background indexing with timestamped understanding.

        Returns immediately. Use ONCE per new media link when its content matters
        beyond this chat; analysis completes in the background within minutes, after
        which search_personal_context returns its timestamped segments.
        :param url: The YouTube or supported media URL to analyze and index.
        """
        clean_url = url.strip()
        if not clean_url:
            return "Please provide a valid media URL."
        native_user_id = self._optional_id(__user__, "id") or "unknown"
        payload = {
            "native_user_id": native_user_id,
            "url": clean_url,
        }
        try:
            response = await self._signed_json_post("/v1/personal-context/process-media", payload)
            if not isinstance(response, dict) or set(response) != {"status", "url", "media_type"}:
                return self._UNAVAILABLE
            if response["status"] == "duplicate":
                return (
                    f"ℹ️ Media already indexed or queued: {response['url']}\n"
                    "Answer questions about it via search_personal_context."
                )
            return (
                f"🎬 Queued for deep media indexing: {clean_url}\n"
                "Background analysis usually takes a few minutes. Once it completes, "
                "call get_media_details with this exact URL to retrieve the full "
                "timestamped breakdown, or answer via search_personal_context."
            )
        except Exception:  # noqa: BLE001
            return self._UNAVAILABLE

    @staticmethod
    def _format_duration(seconds: object) -> str:
        """Render one duration value as H:MM:SS or MM:SS."""
        if isinstance(seconds, bool) or not isinstance(seconds, int | float):
            return ""
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    async def get_media_details(
        self,
        url: str,
        __user__: dict | None = None,
    ) -> str:
        """Retrieve the complete indexed analysis of one processed YouTube/media URL.

        Use this instead of search_personal_context when the user asks about a
        specific video by link or title and you need its full timestamped
        breakdown: transcript-style segments, summary, takeaways, and metadata.
        :param url: The exact media URL previously queued via process_media_url.
        """
        clean_url = url.strip()
        if not clean_url:
            return "Please provide a valid media URL."
        native_user_id = self._optional_id(__user__, "id") or "unknown"
        payload = {
            "native_user_id": native_user_id,
            "url": clean_url,
        }
        try:
            response = await self._signed_json_post("/v1/personal-context/media-detail", payload)
            if not isinstance(response, dict) or not isinstance(response.get("found"), bool):
                return self._UNAVAILABLE
            if not response["found"]:
                return (
                    f"Media is not indexed yet: {response.get('url') or clean_url}\n"
                    "If it was just shared, call process_media_url first, wait a few "
                    "minutes for background analysis, then retry get_media_details."
                )

            lines = [
                f"🎬 Indexed Media: {response.get('title') or clean_url}",
            ]
            channel = response.get("channel_or_author")
            if channel:
                lines.append(f"📺 Channel/Author: {channel}")
            duration = response.get("duration_seconds")
            if duration is not None:
                rendered = self._format_duration(duration)
                if rendered:
                    lines.append(f"⏱️ Duration: {rendered}")
            lines.append(f"🔗 URL: {response.get('url') or clean_url}")

            takeaways = response.get("key_takeaways")
            if isinstance(takeaways, list) and takeaways:
                lines.append("\nKey Takeaways:")
                for takeaway in takeaways:
                    lines.append(f"- {takeaway}")

            segments = response.get("segments")
            if isinstance(segments, list) and segments:
                lines.append("\nTimestamped Breakdown:")
                for segment in segments:
                    if not isinstance(segment, dict):
                        continue
                    start = segment.get("start_time_seconds")
                    end = segment.get("end_time_seconds")
                    span = self._media_time_str(start, end).removeprefix(" @ ")
                    label = segment.get("label")
                    header = f"[{span}]" if span else ""
                    if label:
                        header = f"{header} {label}:"
                    lines.append(f"\n{header}".strip())
                    content = segment.get("content")
                    if isinstance(content, str):
                        lines.append(content)
            else:
                lines.append("\nNo timestamped segments stored yet.")

            return "\n".join(lines)
        except Exception:  # noqa: BLE001 - optional media detail must fail open.
            return self._UNAVAILABLE
