import hashlib
from typing import Any

import httpx
import structlog

LOGGER = structlog.get_logger("assistant_core.files.client")


class OpenWebUIFileFetchError(Exception):
    """Raised when fetching file metadata or content from Open WebUI fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        or d.get("source_path")
        or d.get("target_path")
        or d.get("rel_path")
        or d.get("relative_path")
    )
    if not fname and isinstance(d.get("name"), str):
        raw_name = str(d["name"])
        if "." in raw_name or "/" in raw_name or "\\" in raw_name:
            fname = raw_name

    if fname and isinstance(fname, str):
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
                            _extract_files_from_dict(o_data, kb_file_ids, kb_hashes, kb_filenames)
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

    # Check direct metadata markers
    source_val = str(data.get("source") or meta_dict.get("source") or "").strip().lower()
    type_val = str(data.get("type") or meta_dict.get("type") or "").strip().lower()
    marker_fields = {
        "collection_id",
        "collection_name",
        "knowledge_id",
        "knowledge_name",
    }
    matched_markers = sorted(
        field for field in marker_fields if data.get(field) or meta_dict.get(field)
    )
    if (
        matched_markers
        or source_val in ("knowledge", "collection", "kb", "vault")
        or type_val in ("collection", "knowledge", "kb", "vault")
    ):
        LOGGER.info(
            "fetch_openwebui_file_skipped_kb_metadata",
            file_id=file_id,
            source=source_val,
            type=type_val,
            matched=matched_markers,
            data_keys=sorted(k for k in data if k != "data"),
            meta_keys=sorted(meta_dict.keys()),
        )
        return None

    filename = str(data.get("filename") or meta_dict.get("name") or file_id)
    mime_type = str(meta_dict.get("content_type") or "text/plain")

    # Check against knowledge registry
    kb_file_ids: set[str] = set()
    kb_hashes: set[str] = set()
    kb_filenames: set[str] = set()
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            kb_resp = client.get(f"{clean_url}/api/v1/knowledge/", headers=headers)
            if kb_resp.status_code == 200:
                kb_data = kb_resp.json()
                kb_list: list[dict[str, Any]] = []
                if isinstance(kb_data, list):
                    kb_list = [item for item in kb_data if isinstance(item, dict)]
                elif isinstance(kb_data, dict):
                    if isinstance(kb_data.get("items"), list):
                        kb_list = [item for item in kb_data["items"] if isinstance(item, dict)]
                    elif isinstance(kb_data.get("data"), list):
                        kb_list = [item for item in kb_data["data"] if isinstance(item, dict)]
                    else:
                        kb_list = [kb_data]

                for item in kb_list:
                    kb_name = item.get("name")
                    if kb_name:
                        kb_filenames.add(str(kb_name).strip())
                    _extract_files_from_dict(item, kb_file_ids, kb_hashes, kb_filenames)
                    kb_id = item.get("id")
                    if kb_id:
                        try:
                            detail_resp = client.get(
                                f"{clean_url}/api/v1/knowledge/{kb_id}", headers=headers
                            )
                            if detail_resp.status_code == 200 and isinstance(
                                detail_resp.json(), dict
                            ):
                                _extract_files_from_dict(
                                    detail_resp.json(), kb_file_ids, kb_hashes, kb_filenames
                                )
                        except Exception:  # noqa: BLE001, S110
                            pass
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("fetch_openwebui_file_kb_registry_check_failed", error=str(exc))

    clean_fname = filename.strip()
    base_fname = clean_fname.replace("\\", "/").split("/")[-1].strip()
    if file_id in kb_file_ids or clean_fname in kb_filenames or base_fname in kb_filenames:
        LOGGER.info(
            "fetch_openwebui_file_skipped_kb_registry_match",
            file_id=file_id,
            filename=filename,
        )
        return None

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

    if content.strip():
        c_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if c_hash in kb_hashes:
            LOGGER.info(
                "fetch_openwebui_file_skipped_kb_hash_match",
                file_id=file_id,
                filename=filename,
                content_sha256=c_hash,
            )
            return None

    return filename, mime_type, content
