import time
from typing import Any

import httpx
import structlog

LOGGER = structlog.get_logger("assistant_core.files.client")

_KB_REGISTRY_CACHE: tuple[float, set[str]] = (0.0, set())


class OpenWebUIFileFetchError(Exception):
    """Raised when fetching file metadata or content from Open WebUI fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _fetch_kb_file_ids(
    clean_url: str,
    headers: dict[str, str],
    timeout_seconds: float = 10.0,
) -> set[str]:
    """Fetch and cache file IDs belonging to any Open WebUI Knowledge Base."""
    global _KB_REGISTRY_CACHE
    now = time.time()
    cache_time, cached_ids = _KB_REGISTRY_CACHE
    if now - cache_time < 30.0 and cached_ids:
        return cached_ids

    kb_file_ids: set[str] = set()
    try:
        with httpx.Client(timeout=min(timeout_seconds, 10.0)) as client:
            resp = client.get(f"{clean_url}/api/v1/knowledge/", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        files = item.get("files")
                        if isinstance(files, list):
                            for f in files:
                                if isinstance(f, dict) and f.get("id"):
                                    kb_file_ids.add(str(f["id"]))
                                elif isinstance(f, str) and f:
                                    kb_file_ids.add(f)
                        data_field = item.get("data")
                        if isinstance(data_field, dict):
                            file_ids = data_field.get("file_ids")
                            if isinstance(file_ids, list):
                                for fid in file_ids:
                                    if fid:
                                        kb_file_ids.add(str(fid))
        _KB_REGISTRY_CACHE = (now, kb_file_ids)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("openwebui_knowledge_registry_lookup_failed", error=str(exc))

    return kb_file_ids


def fetch_openwebui_file(
    *,
    base_url: str,
    api_key: str | None,
    file_id: str,
    timeout_seconds: float = 30.0,
) -> tuple[str, str, str] | None:
    """Fetch (filename, mime_type, markdown_content) for a given Open WebUI file ID.

    Returns:
        tuple[filename, mime_type, markdown_content] if direct user file, or None if knowledge base document.
    """
    clean_url = base_url.rstrip("/")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # 1. Fetch file metadata
    meta_url = f"{clean_url}/api/v1/files/{file_id}"
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(meta_url, headers=headers)
            if resp.status_code == 404:
                raise OpenWebUIFileFetchError(
                    f"File {file_id} not found in Open WebUI", status_code=404
                )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
    except httpx.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise OpenWebUIFileFetchError(
            f"Failed to fetch metadata for file {file_id}: {exc}", status_code=status
        ) from exc

    raw_meta = data.get("meta")
    meta_dict: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}

    # Check if the file is part of an Open WebUI Knowledge Base / Collection
    meta_src = str(meta_dict.get("source") or "")
    data_src = str(data.get("source") or "")
    meta_tp = str(meta_dict.get("type") or "")
    data_tp = str(data.get("type") or "")
    meta_tags = meta_dict.get("tags") or []
    tag_strings = {str(t).lower() for t in meta_tags} if isinstance(meta_tags, list) else set()

    is_kb_file = bool(
        meta_dict.get("collection_name")
        or data.get("collection_name")
        or meta_dict.get("knowledge_id")
        or data.get("knowledge_id")
        or meta_dict.get("kb_id")
        or data.get("kb_id")
        or meta_dict.get("collection_id")
        or data.get("collection_id")
        or meta_src in ("knowledge", "collection", "rag", "external")
        or data_src in ("knowledge", "collection", "rag", "external")
        or meta_tp in ("collection", "knowledge", "doc", "web", "note", "folder")
        or data_tp in ("collection", "knowledge", "doc", "web", "note", "folder")
        or bool(tag_strings & {"knowledge", "collection", "kb", "rag"})
    )

    if not is_kb_file:
        kb_file_ids = _fetch_kb_file_ids(clean_url, headers, timeout_seconds)
        if file_id in kb_file_ids:
            is_kb_file = True

    if is_kb_file:
        LOGGER.info(
            "skipping_knowledge_base_file",
            file_id=file_id,
            filename=str(data.get("filename") or meta_dict.get("name") or file_id),
            collection_name=meta_dict.get("collection_name") or data.get("collection_name"),
        )
        return None

    filename = str(data.get("filename") or meta_dict.get("name") or file_id)
    mime_type = str(meta_dict.get("content_type") or "text/plain")

    # Check if extracted markdown content is already present in data.content
    content = ""
    file_data = data.get("data")
    if isinstance(file_data, dict):
        content_val = file_data.get("content")
        if isinstance(content_val, str) and content_val.strip():
            content = content_val

    # If not present in JSON data, fetch /api/v1/files/{id}/content
    if not content.strip():
        content_url = f"{clean_url}/api/v1/files/{file_id}/content"
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                content_resp = client.get(content_url, headers=headers)
                content_resp.raise_for_status()
                content = content_resp.text
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise OpenWebUIFileFetchError(
                f"Failed to fetch content for file {file_id}: {exc}", status_code=status
            ) from exc

    return filename, mime_type, content
