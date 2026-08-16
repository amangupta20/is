"""Unit tests for file segment and reference database models and migration."""

from assistant_core.files.models import FileReference, FileSegment


def test_file_segment_model_attributes() -> None:
    """FileSegment model has correct table, schema, and column constraints."""
    table = FileSegment.__table__
    assert table.schema == "assistant_core"
    assert table.name == "file_segment"

    cols = table.c
    assert cols.id.primary_key
    assert not cols.user_id.nullable
    assert cols.content_sha256.type.length == 64
    assert cols.chunking_version.type.length == 40
    assert not cols.content.nullable
    assert cols.embedding_dimension.type.python_type is int


def test_file_reference_model_attributes() -> None:
    """FileReference model has correct table, schema, and foreign keys."""
    table = FileReference.__table__
    assert table.schema == "assistant_core"
    assert table.name == "file_reference"

    cols = table.c
    assert cols.id.primary_key
    assert not cols.user_id.nullable
    assert not cols.segment_id.nullable
    assert cols.native_file_id.type.length == 200
    assert cols.filename.type.length == 500
    assert cols.header_path.type.length == 500
    assert cols.chunk_ordinal.type.python_type is int
    assert cols.tombstoned_at.nullable
