"""Unit tests for the markdown-aware chunking engine (markdown-v1)."""

import hashlib

from assistant_core.files.chunking import chunk_markdown
from assistant_core.files.schemas import MarkdownChunk


def test_chunk_markdown_short_document() -> None:
    """A small markdown document produces a single chunk with preserved header breadcrumb."""
    doc = "# Introduction\n\nThis is a simple overview of the system."
    chunks = chunk_markdown(doc)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert isinstance(chunk, MarkdownChunk)
    assert chunk.chunk_ordinal == 0
    assert chunk.header_path == "Introduction"
    assert "This is a simple overview of the system." in chunk.text
    expected_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
    assert chunk.content_sha256 == expected_hash


def test_chunk_markdown_nested_headers() -> None:
    """Nested headers build a hierarchical breadcrumb path."""
    doc = """# Architecture
Overview text here.

## Database
Database section text.

### PostgreSQL
Details about PostgreSQL and pgvector.
"""
    chunks = chunk_markdown(doc)
    assert len(chunks) == 3
    assert chunks[0].header_path == "Architecture"
    assert chunks[1].header_path == "Architecture > Database"
    assert chunks[2].header_path == "Architecture > Database > PostgreSQL"
    assert "Details about PostgreSQL" in chunks[2].text


def test_chunk_markdown_preserves_atomic_tables() -> None:
    """Markdown tables under 4000 chars are kept intact and not sliced midway."""
    table_lines = [
        "| ID | Feature | Status |",
        "| :--- | :--- | :--- |",
    ]
    for i in range(10):
        table_lines.append(f"| {i} | Feature description {i} | Done |")
    table_text = "\n".join(table_lines)

    doc = f"# Feature Matrix\n\n{table_text}\n\nConclusion text after table."
    chunks = chunk_markdown(doc, max_chars=4000)
    assert len(chunks) == 1
    assert table_text in chunks[0].text


def test_chunk_markdown_preserves_atomic_fenced_code_blocks() -> None:
    """Fenced code blocks are kept intact."""
    code_block = "```python\ndef hello_world():\n    print('Hello')\n    return True\n```"
    doc = f"# Code Example\n\nHere is python code:\n\n{code_block}\n\nEnd of section."
    chunks = chunk_markdown(doc)
    assert len(chunks) == 1
    assert code_block in chunks[0].text


def test_chunk_markdown_subdivides_large_sections_with_overlap() -> None:
    """A very large section (>4000 chars) is subdivided along paragraphs with overlap."""
    paragraphs = [f"Paragraph {i}: " + ("word " * 60) + "\n\n" for i in range(30)]
    long_doc = "# Massive Guide\n\n" + "".join(paragraphs)
    chunks = chunk_markdown(long_doc, max_chars=1000, overlap_chars=100)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) <= 1000
        assert chunk.header_path == "Massive Guide"
        assert chunk.content_sha256 == hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
    # Check ordinals are sequential
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_ordinal == i


def test_chunk_markdown_empty_or_whitespace() -> None:
    """Empty or whitespace-only documents return an empty list of chunks."""
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  \t ") == []
