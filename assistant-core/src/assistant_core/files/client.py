import hashlib
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


def _extract_files_from_dict(
    d: dict[str, Any],
    kb_file_ids: set[str],
    kb_hashes: set[str],
    kb_filenames: set[str],
) -> None:
    """Recursively extract file paths, filenames, hashes, and IDs from arbitrary API payload dictionaries."""
    fname = (
        d.get("file_path")
        or d.get("path")
        or d.get("filename")
        or d.get("name")
        or d.get("source_path")
        or d.get("target_path")
    )
    if fname and isinstance(fname, str) and ("." in fname or "/" in fname or "\\" in fname):
        clean_fname = fname.strip()
        base_fname = clean_fname.replace("\\", "/").split("/")[-1].strip()
        if clean_fname:
            kb_filenames.add(clean_fname)
        if base_fname:
            kb_filenames.add(base_fname)

    f_hash = (
        d.get("content_hash")
        or d.get("hash")
        or d.get("sha256")
        or d.get("git_sha")
        or d.get("sha")
        or d.get("file_hash")
    )
    if f_hash and isinstance(f_hash, str) and len(f_hash.strip()) >= 7:
        kb_hashes.add(f_hash.strip())

    fid = d.get("file_id") or d.get("openwebui_file_id")
    if fid and isinstance(fid, str) and fid.strip():
        kb_file_ids.add(fid.strip())
    elif d.get("id") and isinstance(d.get("id"), str) and (fname or d.get("meta") or d.get("hash")):
        kb_file_ids.add(str(d["id"]).strip())

    for k, v in d.items():
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    _extract_files_from_dict(item, kb_file_ids, kb_hashes, kb_filenames)
                elif isinstance(item, str) and item.strip():
                    clean_str = item.strip()
                    if k in ("file_ids", "files"):
                        kb_file_ids.add(clean_str)
                    elif "." in clean_str or "/" in clean_str or "\\" in clean_str:
                        kb_filenames.add(clean_str)
                        kb_filenames.add(clean_str.replace("\\", "/").split("/")[-1].strip())
        elif isinstance(v, dict):
            _extract_files_from_dict(v, kb_file_ids, kb_hashes, kb_filenames)


def fetch_all_kb_metadata_and_hashes(
    *,
    base_url: str,
    api_key: str | None,
    oikb_url: str | None = None,
    oikb_api_key: str | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[set[str], set[str], set[str]]:
    """Fetch all file IDs, content SHA256 hashes, and filenames belonging to Open WebUI Knowledge Bases and oikb.

    Returns:
        tuple[set(kb_file_ids), set(kb_content_hashes), set(kb_filenames)]
    """
    clean_url = base_url.rstrip("/")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    kb_file_ids: set[str] = set()
    kb_hashes: set[str] = set()
    kb_filenames: set[str] = set()

    # 1. Query Open WebUI Knowledge Bases and Files registry
    LOGGER.info(
        "kb_reconciliation_scanning_openwebui",
        url=clean_url,
        has_api_key=bool(api_key),
    )
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(f"{clean_url}/api/v1/knowledge/", headers=headers)
            LOGGER.info(
                "openwebui_knowledge_registry_response",
                status_code=resp.status_code,
                body_preview=resp.text[:300] if resp.text else "",
            )
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"Open WebUI authentication failed (HTTP {resp.status_code}). Please configure ASSISTANT_OPEN_WEBUI_API_KEY in environment."
                )
            if resp.status_code == 200:
                data = resp.json()
                kb_list: list[dict[str, Any]] = []
                if isinstance(data, list):
                    kb_list = [item for item in data if isinstance(item, dict)]
                elif isinstance(data, dict):
                    if isinstance(data.get("items"), list):
                        kb_list = [item for item in data["items"] if isinstance(item, dict)]
                    elif isinstance(data.get("data"), list):
                        kb_list = [item for item in data["data"] if isinstance(item, dict)]
                    else:
                        kb_list = [data]

                LOGGER.info(
                    "openwebui_knowledge_collections_count",
                    count=len(kb_list),
                    collections=[item.get("name") for item in kb_list],
                )

                for item in kb_list:
                    kb_id = item.get("id")
                    kb_name = item.get("name")
                    if kb_name:
                        kb_filenames.add(str(kb_name).strip())

                    _extract_files_from_dict(item, kb_file_ids, kb_hashes, kb_filenames)

                    if kb_id:
                        try:
                            kb_resp = client.get(
                                f"{clean_url}/api/v1/knowledge/{kb_id}", headers=headers
                            )
                            if kb_resp.status_code == 200:
                                kb_data = kb_resp.json()
                                if isinstance(kb_data, dict):
                                    _extract_files_from_dict(
                                        kb_data, kb_file_ids, kb_hashes, kb_filenames
                                    )
                        except Exception as kb_exc:  # noqa: BLE001
                            LOGGER.info("fetch_kb_detail_failed", kb_id=kb_id, error=str(kb_exc))

                        try:
                            kb_files_resp = client.get(
                                f"{clean_url}/api/v1/knowledge/{kb_id}/files", headers=headers
                            )
                            if kb_files_resp.status_code == 200:
                                kb_files_data = kb_files_resp.json()
                                if isinstance(kb_files_data, list):
                                    for kbf in kb_files_data:
                                        if isinstance(kbf, dict):
                                            _extract_files_from_dict(
                                                kbf, kb_file_ids, kb_hashes, kb_filenames
                                            )
                                elif isinstance(kb_files_data, dict):
                                    _extract_files_from_dict(
                                        kb_files_data, kb_file_ids, kb_hashes, kb_filenames
                                    )
                        except Exception as kbf_exc:  # noqa: BLE001
                            LOGGER.info(
                                "fetch_kb_files_endpoint_failed",
                                kb_id=kb_id,
                                error=str(kbf_exc),
                            )

            # Also check GET /api/v1/files/ for any files linked to knowledge/collections
            try:
                all_files_resp = client.get(f"{clean_url}/api/v1/files/", headers=headers)
                if all_files_resp.status_code == 200:
                    files_payload = all_files_resp.json()
                    files_list: list[dict[str, Any]] = []
                    if isinstance(files_payload, list):
                        files_list = [f for f in files_payload if isinstance(f, dict)]
                    elif isinstance(files_payload, dict):
                        if isinstance(files_payload.get("items"), list):
                            files_list = [
                                f for f in files_payload["items"] if isinstance(f, dict)
                            ]
                        elif isinstance(files_payload.get("data"), list):
                            files_list = [
                                f for f in files_payload["data"] if isinstance(f, dict)
                            ]

                    LOGGER.info("openwebui_all_files_count", total_files=len(files_list))
                    for f_entry in files_list:
                        f_meta = f_entry.get("meta") or {}
                        if (
                            isinstance(f_meta, dict)
                            and (
                                f_meta.get("collection_name")
                                or f_meta.get("knowledge_id")
                                or f_meta.get("collection_id")
                                or f_entry.get("type") in ("knowledge", "collection")
                                or f_meta.get("type") in ("knowledge", "collection")
                            )
                        ):
                            _extract_files_from_dict(
                                f_entry, kb_file_ids, kb_hashes, kb_filenames
                            )
            except Exception as files_exc:  # noqa: BLE001
                LOGGER.info("fetch_all_files_list_failed", error=str(files_exc))

            # Fetch file metadata and hashes for discovered KB files
            for fid in list(kb_file_ids):
                try:
                    f_resp = client.get(f"{clean_url}/api/v1/files/{fid}", headers=headers)
                    if f_resp.status_code == 200:
                        f_data = f_resp.json()
                        if isinstance(f_data, dict):
                            fname = f_data.get("filename")
                            if fname:
                                kb_filenames.add(str(fname).strip())
                            raw_meta = f_data.get("meta")
                            if isinstance(raw_meta, dict):
                                if raw_meta.get("hash"):
                                    kb_hashes.add(str(raw_meta["hash"]).strip())
                                if raw_meta.get("name"):
                                    kb_filenames.add(str(raw_meta["name"]).strip())
                            content = (
                                f_data.get("data", {}).get("content")
                                if isinstance(f_data.get("data"), dict)
                                else None
                            )
                            if isinstance(content, str) and content.strip():
                                c_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                                kb_hashes.add(c_hash)
                except Exception as file_exc:  # noqa: BLE001
                    LOGGER.info("fetch_kb_file_detail_failed", file_id=fid, error=str(file_exc))

    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("fetch_all_kb_metadata_failed", error=str(exc))

    # 2. Query oikb directly (Obsidian/OpenWebUI KB Sync service)
    target_oikb_url = oikb_url or "http://oikb:8080"
    oikb_headers: dict[str, str] = {}
    if oikb_api_key:
        oikb_headers["Authorization"] = f"Bearer {oikb_api_key}"
        oikb_headers["X-API-Key"] = oikb_api_key

    LOGGER.info(
        "kb_reconciliation_scanning_oikb",
        url=target_oikb_url,
        has_api_key=bool(oikb_api_key),
    )

    try:
        with httpx.Client(timeout=timeout_seconds) as oikb_client:
            for path in (
                "/sync/history",
                "/history",
                "/sync/status",
                "/status",
                "/files",
                "/api/v1/history",
            ):
                try:
                    o_resp = oikb_client.get(
                        f"{target_oikb_url.rstrip('/')}{path}", headers=oikb_headers
                    )
                    LOGGER.info("oikb_endpoint_response", path=path, status_code=o_resp.status_code)
                    if o_resp.status_code == 200:
                        o_data = o_resp.json()
                        if isinstance(o_data, dict):
                            _extract_files_from_dict(
                                o_data, kb_file_ids, kb_hashes, kb_filenames
                            )
                        elif isinstance(o_data, list):
                            for el in o_data:
                                if isinstance(el, dict):
                                    _extract_files_from_dict(
                                        el, kb_file_ids, kb_hashes, kb_filenames
                                    )
                except Exception as path_exc:  # noqa: BLE001
                    LOGGER.info("fetch_oikb_path_failed", path=path, error=str(path_exc))
    except Exception as oikb_exc:  # noqa: BLE001
        LOGGER.warning("fetch_oikb_metadata_failed", error=str(oikb_exc))

    LOGGER.info(
        "kb_metadata_scan_completed",
        total_kb_file_ids=len(kb_file_ids),
        total_kb_hashes=len(kb_hashes),
        total_kb_filenames=len(kb_filenames),
    )

    return kb_file_ids, kb_hashes, kb_filenames


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
