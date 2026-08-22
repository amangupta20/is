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
        self.capture.setdefault("requests", []).append({"url": url, **kwargs})
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
        lambda *args, **kwargs: _RecordingClient(capture, kwargs.get("timeout", 10.0), mock_resp),
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
        lambda *args, **kwargs: _RecordingClient(capture, kwargs.get("timeout", 10.0), mock_resp),
    )

    sections_json = json.dumps(
        [{"heading": "Executive Summary", "paragraphs": ["All systems normal."]}]
    )
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
    assert (
        "http://assistant-core:8080/v1/artifacts/22222222-2222-2222-2222-222222222222/download"
        in result
    )


def test_create_document_tool_with_public_assistant_url(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.open_webui_url = ""  # No Open WebUI upload
    tools.valves.public_assistant_url = "https://mem.app.amhl.ovh"

    capture: dict[str, Any] = {}
    mock_resp = {
        "id": "44444444-4444-4444-4444-444444444444",
        "current_version_num": 1,
        "base64_data": "UEsDBBQAAAA=",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "download_url": "http://assistant-core:8080/v1/artifacts/44444444-4444-4444-4444-444444444444/download",
    }

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _RecordingClient(capture, kwargs.get("timeout", 10.0), mock_resp),
    )

    sections_json = json.dumps(
        [{"heading": "Executive Summary", "paragraphs": ["All systems normal."]}]
    )
    result = asyncio.run(
        tools.create_document(
            title="Public Spec",
            sections_json=sections_json,
            __user__={"id": "user-123"},
        )
    )

    assert "DOCX Created" in result
    assert (
        "https://mem.app.amhl.ovh/v1/artifacts/44444444-4444-4444-4444-444444444444/download"
        in result
    )


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
        lambda *args, **kwargs: _RecordingClient(capture, kwargs.get("timeout", 10.0), mock_resp),
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


def test_create_pdf_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.open_webui_url = "http://localhost:8080"
    tools.valves.open_webui_api_key = "test-owui-key"

    capture: dict[str, Any] = {}
    mock_resp = {
        "id": "55555555-5555-5555-5555-555555555555",
        "current_version_num": 1,
        "base64_data": "JVBERi0xLjQK",
        "mime_type": "application/pdf",
        "download_url": "http://assistant-core:8080/v1/artifacts/55555555-5555-5555-5555-555555555555/download",
    }

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _RecordingClient(capture, kwargs.get("timeout", 10.0), mock_resp),
    )

    sections_json = json.dumps(
        [{"heading": "Executive Summary", "paragraphs": ["All systems normal."]}]
    )
    result = asyncio.run(
        tools.create_pdf(
            title="Whitepaper",
            sections_json=sections_json,
            subtitle="Confidential",
            theme="navy",
            __user__={"id": "user-123", "token": "user-token-abc"},
        )
    )

    assert "PDF Created" in result
    assert "Whitepaper.pdf" in result
    assert "/api/v1/files/owui-file-999/content" in result


def test_create_typst_document_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.open_webui_url = "http://localhost:8080"
    tools.valves.open_webui_api_key = "test-owui-key"

    capture: dict[str, Any] = {}
    mock_resp = {
        "id": "66666666-6666-6666-6666-666666666666",
        "current_version_num": 1,
        "base64_data": "JVBERi0xLjQK",
        "mime_type": "application/pdf",
        "download_url": "http://assistant-core:8080/v1/artifacts/66666666-6666-6666-6666-666666666666/download",
        "onlyoffice_url": "http://onlyoffice/session-123",
    }

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _RecordingClient(capture, kwargs.get("timeout", 10.0), mock_resp),
    )

    markup = "= Research Report\n\n== Introduction\nTypst compiled document."
    result = asyncio.run(
        tools.create_typst_document(
            title="Research Report",
            markup=markup,
            change_summary="Initial typst draft",
            __user__={"id": "user-123", "token": "user-token-abc"},
            __metadata__={"folder_id": "folder-99", "project_id": "proj-88"},
        )
    )

    assert "PDF Created" in result
    assert "Research Report.pdf" in result
    assert "/api/v1/files/owui-file-999/content" in result
    assert "http://onlyoffice/session-123" in result

    first_req = capture["requests"][0]
    assert first_req["url"] == "http://assistant-core:8080/v1/artifacts/create"
    sent_payload = json.loads(first_req["content"])
    assert sent_payload["artifact_type"] == "typst"
    assert sent_payload["raw_content"] == markup
    assert sent_payload["title"] == "Research Report"
    assert sent_payload["native_user_id"] == "user-123"
    assert sent_payload["native_folder_id"] == "folder-99"
    assert sent_payload["native_project_id"] == "proj-88"
    assert sent_payload["change_summary"] == "Initial typst draft"


def test_create_resume_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    tools = module.Tools()
    tools.valves.hmac_secret = "test-secret"
    tools.valves.open_webui_url = "http://localhost:8080"
    tools.valves.open_webui_api_key = "test-owui-key"

    capture: dict[str, Any] = {}
    mock_resp = {
        "id": "77777777-7777-7777-7777-777777777777",
        "current_version_num": 1,
        "base64_data": "JVBERi0xLjQK",
        "mime_type": "application/pdf",
        "download_url": "http://assistant-core:8080/v1/artifacts/77777777-7777-7777-7777-777777777777/download",
    }

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _RecordingClient(capture, kwargs.get("timeout", 10.0), mock_resp),
    )

    resume_spec = {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "summary": "Distributed Systems Engineer",
        "experience": [
            {
                "company": "Tech Corp",
                "position": "Staff Engineer",
                "start_date": "2022-01",
                "highlights": ["Scaled backend to 100k rps"],
            }
        ],
        "education": [
            {
                "institution": "University of Tech",
                "degree": "B.S. Computer Science",
                "start_date": "2018",
                "end_date": "2022",
            }
        ],
        "skills": [{"name": "Languages", "skills": ["Python", "Rust", "Go"]}],
    }

    result = asyncio.run(
        tools.create_resume(
            title="Jane Doe Resume",
            resume_json=json.dumps(resume_spec),
            change_summary="Version 1",
            __user__={"id": "user-123", "token": "user-token-abc"},
        )
    )

    assert "PDF Created" in result
    assert "Jane Doe Resume.pdf" in result
    assert "/api/v1/files/owui-file-999/content" in result

    first_req = capture["requests"][0]
    assert first_req["url"] == "http://assistant-core:8080/v1/artifacts/create"
    sent_payload = json.loads(first_req["content"])
    assert sent_payload["artifact_type"] == "resume"
    assert sent_payload["resume_spec"] == resume_spec
    assert sent_payload["title"] == "Jane Doe Resume"
    assert sent_payload["native_user_id"] == "user-123"
    assert sent_payload["change_summary"] == "Version 1"
