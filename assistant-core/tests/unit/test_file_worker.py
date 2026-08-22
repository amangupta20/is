"""Unit tests for the asynchronous file indexing worker."""

import uuid
from unittest.mock import patch

import anyio
import pytest

from assistant_core.files.client import OpenWebUIFileFetchError
from assistant_core.files.schemas import FileMaterializationResult
from assistant_core.jobs.worker import _handle_index_file


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def test_handle_index_file_success() -> None:
    user_id = uuid.uuid4()
    file_id = "test-file-123"
    payload = {
        "file_id": file_id,
        "user_id": str(user_id),
    }

    session = FakeSession()

    with (
        patch("assistant_core.jobs.worker.fetch_openwebui_file") as mock_fetch,
        patch("assistant_core.jobs.worker.materialize_file_passages") as mock_mat,
        patch("assistant_core.jobs.worker.get_conversation_embedder"),
    ):
        mock_fetch.return_value = (
            "report.docx",
            "application/vnd.openxmlformats",
            "# Heading\n\nContent",
        )
        mock_mat.return_value = FileMaterializationResult(
            native_file_id=file_id,
            total_chunks=1,
            inserted_segments=1,
            reused_segments=0,
            missing_embedding_segment_ids=(),
        )

        async def exercise() -> None:
            await _handle_index_file(session, payload)  # type: ignore[arg-type]

        anyio.run(exercise)
        assert mock_fetch.called
        assert mock_mat.called


def test_handle_index_file_skips_kb_document() -> None:
    """When fetch_openwebui_file returns None (KB document), indexing is skipped without error."""
    user_id = uuid.uuid4()
    file_id = "test-kb-doc-456"
    payload = {
        "file_id": file_id,
        "user_id": str(user_id),
    }

    session = FakeSession()

    with (
        patch("assistant_core.jobs.worker.fetch_openwebui_file") as mock_fetch,
        patch("assistant_core.jobs.worker.materialize_file_passages") as mock_mat,
    ):
        mock_fetch.return_value = None  # Skipped KB document

        async def exercise() -> None:
            await _handle_index_file(session, payload)  # type: ignore[arg-type]

        anyio.run(exercise)
        assert mock_fetch.called
        assert not mock_mat.called
        assert not session.committed


def test_handle_index_file_fetch_error() -> None:
    user_id = uuid.uuid4()
    file_id = "test-file-123"
    payload = {
        "file_id": file_id,
        "user_id": str(user_id),
    }

    session = FakeSession()

    with patch("assistant_core.jobs.worker.fetch_openwebui_file") as mock_fetch:
        mock_fetch.side_effect = OpenWebUIFileFetchError("404 Not Found", status_code=404)

        async def exercise() -> None:
            await _handle_index_file(session, payload)  # type: ignore[arg-type]

        with pytest.raises(OpenWebUIFileFetchError):
            anyio.run(exercise)


def test_fetch_openwebui_file_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_openwebui_file retrieves filename, mime_type, and markdown content."""
    import httpx

    from assistant_core.files.client import fetch_openwebui_file

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/files/chat-file-id":
            return httpx.Response(
                200,
                json={
                    "id": "chat-file-id",
                    "filename": "guidelines.pdf",
                    "meta": {"content_type": "application/pdf"},
                    "data": {"content": "Extracted PDF text content"},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        "httpx.Client",
        lambda *args, **kwargs: real_client(transport=transport),
    )

    result = fetch_openwebui_file(
        base_url="http://openwebui:8080",
        api_key=None,
        file_id="chat-file-id",
    )
    assert result is not None
    assert result[0] == "guidelines.pdf"
    assert result[1] == "application/pdf"
    assert result[2] == "Extracted PDF text content"
