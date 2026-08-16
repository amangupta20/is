"""Contract tests for the standalone Assistant Core Documents & Spreadsheets Tool."""

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


def _module():
    path = Path(__file__).parents[1] / "assistant_core_artifacts_tool.py"
    if not path.exists():
        pytest.fail("Assistant Core Artifacts Tool module is missing")
    spec = importlib.util.spec_from_file_location("assistant_core_artifacts_tool", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, response_json: object, status_code: int = 200) -> None:
        self.response_json = response_json
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.response_json


class _RecordingClient:
    def __init__(self, capture: dict[str, Any], timeout: float, response_json: object) -> None:
        capture["timeout"] = timeout
        self.capture = capture
        self.response_json = response_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self.capture["url"] = url
        self.capture.update(kwargs)
        if "/api/v1/files/" in url:
            return _Response({"id": "owui-file-999", "filename": "test.xlsx"})
        return _Response(self.response_json)


def test_create_spreadsheet_tool_with_openwebui_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.assistant_core_url = "http://assistant-core:8080"
    tools.valves.open_webui_url = "http://localhost:8080"
    tools.valves.open_webui_api_key = "test-owui-key"

    capture: dict[str, Any] = {}
    mock_resp = {
        "id": "11111111-1111-1111-1111-111111111111",
        "current_version_num": 1,
        "base64_data": "UEsDBBQAAAA=",
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "download_url": "http://assistant-core:8080/v1/artifacts/11111111-1111-1111-1111-111111111111/download",
    }

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout: _RecordingClient(capture, timeout, mock_resp),
    )

    sheets_json = json.dumps([{"name": "Summary", "headers": ["A", "B"], "rows": [[1, 2]]}])
    result = asyncio.run(
        tools.create_spreadsheet(
            title="Q1 Report",
            sheets_json=sheets_json,
            __user__={"id": "user-123", "token": "user-token-abc"},
        )
    )

    assert "XLSX Created" in result
    assert "Q1 Report.xlsx" in result
    assert "/api/v1/files/owui-file-999/content" in result
    assert "base64" not in result


def test_create_document_tool_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.open_webui_url = ""  # No Open WebUI URL

    capture: dict[str, Any] = {}
    mock_resp = {
        "id": "22222222-2222-2222-2222-222222222222",
        "current_version_num": 1,
        "base64_data": "UEsDBBQAAAA=",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "download_url": "http://assistant-core:8080/v1/artifacts/22222222-2222-2222-2222-222222222222/download",
    }

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout: _RecordingClient(capture, timeout, mock_resp),
    )

    sections_json = json.dumps([{"heading": "Executive Summary", "paragraphs": ["All systems normal."]}])
    result = asyncio.run(
        tools.create_document(
            title="Project Brief",
            sections_json=sections_json,
            subtitle="Internal Only",
            __user__={"id": "user-123"},
        )
    )

    assert "DOCX Created" in result
    assert "Project Brief.docx" in result
    assert "http://assistant-core:8080/v1/artifacts/22222222-2222-2222-2222-222222222222/download" in result


def test_create_presentation_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.open_webui_url = "http://localhost:8080"
    tools.valves.open_webui_api_key = "test-owui-key"

    capture: dict[str, Any] = {}
    mock_resp = {
        "id": "33333333-3333-3333-3333-333333333333",
        "current_version_num": 1,
        "base64_data": "UEsDBBQAAAA=",
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "download_url": "http://assistant-core:8080/v1/artifacts/33333333-3333-3333-3333-333333333333/download",
    }

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda timeout: _RecordingClient(capture, timeout, mock_resp),
    )

    slides_json = json.dumps([{"title": "Overview", "bullets": ["Point 1", "Point 2"]}])
    result = asyncio.run(
        tools.create_presentation(
            title="Pitch Deck",
            slides_json=slides_json,
            __user__={"id": "user-123", "token": "user-token-abc"},
        )
    )

    assert "PPTX Created" in result
    assert "Pitch Deck.pptx" in result
    assert "/api/v1/files/owui-file-999/content" in result
