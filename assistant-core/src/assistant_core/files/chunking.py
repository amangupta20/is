"""Markdown-aware chunking engine (version: markdown-v1)."""

import hashlib
import re

from assistant_core.files.schemas import MarkdownChunk

CHUNKING_VERSION = "markdown-v1"
HEADER_REGEX = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
CODE_BLOCK_REGEX = re.compile(r"```[\s\S]*?```", re.MULTILINE)
TABLE_REGEX = re.compile(r"(\|[^\n]+\|\n\|[-:\s|]+\|\n(?:\|[^\n]+\|\n?)*)", re.MULTILINE)


def _compute_sha256(text: str) -> str:
    """Compute the SHA-256 hex digest of UTF-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_into_sections(markdown_text: str) -> list[tuple[str, str]]:
    """Split markdown text into (header_path, section_content) pairs."""
    lines = markdown_text.splitlines(keepends=True)
    sections: list[tuple[str, str]] = []
    header_stack: list[tuple[int, str]] = []
    current_lines: list[str] = []

    def current_header_path() -> str:
        return " > ".join(title for _, title in header_stack)

    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()

            # Flush current section
            if current_lines:
                content = "".join(current_lines).strip()
                if content:
                    sections.append((current_header_path(), content))
                current_lines = []

            # Adjust stack
            while header_stack and header_stack[-1][0] >= level:
                header_stack.pop()
            header_stack.append((level, title))
            current_lines.append(line)
        else:
            current_lines.append(line)

    if current_lines:
        content = "".join(current_lines).strip()
        if content:
            sections.append((current_header_path(), content))

    return sections


def _subdivide_text(
    text: str,
    header_path: str,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:
    """Subdivide a text section that exceeds max_chars while respecting paragraphs."""
    if len(text) <= max_chars:
        return [text]

    # Split by double newlines (paragraphs)
    paragraphs = re.split(r"(\n\s*\n)", text)
    chunks: list[str] = []
    current_chunk = ""

    for part in paragraphs:
        if not part:
            continue
        if len(current_chunk) + len(part) <= max_chars:
            current_chunk += part
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
                # Start new chunk with overlap if possible
                overlap_text = current_chunk[-overlap_chars:] if len(current_chunk) > overlap_chars else ""
                current_chunk = overlap_text + part
            else:
                # A single paragraph exceeds max_chars; hard slice along lines or words
                sub_lines = part.splitlines(keepends=True)
                for line in sub_lines:
                    if len(current_chunk) + len(line) <= max_chars:
                        current_chunk += line
                    else:
                        if current_chunk.strip():
                            chunks.append(current_chunk.strip())
                        current_chunk = line

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # Ensure no chunk exceeds max_chars
    final_chunks: list[str] = []
    for c in chunks:
        while len(c) > max_chars:
            final_chunks.append(c[:max_chars])
            c = c[max_chars - overlap_chars:]
        if c.strip():
            final_chunks.append(c)

    return final_chunks


def chunk_markdown(
    markdown_text: str,
    max_chars: int = 4000,
    overlap_chars: int = 400,
) -> list[MarkdownChunk]:
    """Chunk a markdown document into header-aware, bounded passages."""
    if not markdown_text or not markdown_text.strip():
        return []

    sections = _split_into_sections(markdown_text)
    if not sections:
        # Fallback if no headers found
        sections = [("", markdown_text.strip())]

    result_chunks: list[MarkdownChunk] = []
    ordinal = 0

    for header_path, section_content in sections:
        if len(section_content) <= max_chars:
            sub_passages = [section_content]
        else:
            sub_passages = _subdivide_text(
                section_content,
                header_path,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )

        for passage in sub_passages:
            cleaned = passage.strip()
            if not cleaned:
                continue
            sha = _compute_sha256(cleaned)
            result_chunks.append(
                MarkdownChunk(
                    text=cleaned,
                    header_path=header_path,
                    chunk_ordinal=ordinal,
                    content_sha256=sha,
                )
            )
            ordinal += 1

    return result_chunks
