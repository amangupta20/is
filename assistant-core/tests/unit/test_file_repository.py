"""Unit tests for the file repository."""

import uuid
from typing import Any

import anyio

from assistant_core.files.repository import (
    materialize_file_passages,
    read_file_passage_context,
    search_file_passages,
    tombstone_file_references,
)
from assistant_core.files.schemas import FileHit, FileMaterializationResult, FilePassageContext


class FakeResult:
    def __init__(self, *, scalar: Any = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self.results = results or []
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        if self.results:
            return self.results.pop(0)
        return FakeResult()


def test_materialize_file_passages_inserts_and_reuses() -> None:
    user_id = uuid.uuid4()
    native_file_id = "test-file-123"
    markdown = "# Title\n\nSection content"

    seg_id = uuid.uuid4()
    session = FakeSession(
        [
            FakeResult(scalar=seg_id),  # segment insert returning segment_id
            FakeResult(),  # reference insert
        ]
    )

    async def exercise() -> FileMaterializationResult:
        return await materialize_file_passages(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            native_file_id=native_file_id,
            filename="test.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            markdown_text=markdown,
        )

    res = anyio.run(exercise)

    assert isinstance(res, FileMaterializationResult)
    assert res.native_file_id == native_file_id
    assert res.total_chunks == 1
    assert res.inserted_segments == 1
    assert res.missing_embedding_segment_ids == (str(seg_id),)


def test_tombstone_file_references() -> None:
    user_id = uuid.uuid4()
    native_file_id = "test-file-123"

    session = FakeSession(
        [
            FakeResult(rows=[uuid.uuid4(), uuid.uuid4()]),  # returning 2 references tombstoned
        ]
    )

    async def exercise() -> int:
        return await tombstone_file_references(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            native_file_id=native_file_id,
        )

    count = anyio.run(exercise)
    assert count == 2


def test_search_file_passages_hybrid() -> None:
    user_id = uuid.uuid4()
    ref_id = uuid.uuid4()
    seg_id = uuid.uuid4()

    row = (
        ref_id,
        seg_id,
        "test-file-1",
        "doc.docx",
        "Section 1",
        0,
        "Matching content",
    )
    session = FakeSession(
        [
            FakeResult(rows=[row]),  # lexical search results
            FakeResult(rows=[row]),  # semantic search results
        ]
    )

    async def exercise() -> list[FileHit]:
        return await search_file_passages(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            query_text="matching",
            query_embedding=[0.1] * 1536,
            limit=5,
        )

    hits = anyio.run(exercise)
    assert len(hits) == 1
    assert hits[0].reference_id == str(ref_id)
    assert hits[0].filename == "doc.docx"
    assert hits[0].score > 0.0


def test_read_file_passage_context() -> None:
    user_id = uuid.uuid4()
    ref_id = uuid.uuid4()

    row = (
        ref_id,
        user_id,
        "test-file-1",
        "doc.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Section 1",
        1,
        "Middle content",
    )
    session = FakeSession(
        [
            FakeResult(scalar=row),  # main row
            FakeResult(scalar="Previous chunk content"),  # prev
            FakeResult(scalar="Next chunk content"),  # next
        ]
    )

    async def exercise() -> FilePassageContext | None:
        return await read_file_passage_context(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            reference_id=ref_id,
        )

    ctx = anyio.run(exercise)
    assert ctx is not None
    assert ctx.reference_id == str(ref_id)
    assert ctx.content == "Middle content"
    assert ctx.previous_content == "Previous chunk content"
    assert ctx.next_content == "Next chunk content"
