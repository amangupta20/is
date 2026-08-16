"""Pydantic schemas for typed document, spreadsheet, and presentation specifications."""

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Spreadsheet / XLSX Schemas
# ---------------------------------------------------------------------------
ColumnType = Literal["text", "currency", "percent", "integer", "float", "date"]


class SheetSpec(BaseModel):
    """Specification for one worksheet tab in an Excel workbook."""

    name: str = Field(min_length=1, max_length=31)
    headers: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    column_types: list[ColumnType] | None = None
    totals_row: bool = False


class WorkbookSpec(BaseModel):
    """Complete specification for a multi-tab Excel workbook."""

    title: str = Field(min_length=1, max_length=255)
    sheets: list[SheetSpec] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Document / DOCX Schemas
# ---------------------------------------------------------------------------
class TableSpec(BaseModel):
    """Structured data table to embed in a Word document."""

    headers: list[str]
    rows: list[list[str]]


class DocumentSectionSpec(BaseModel):
    """One logical section of a Word document."""

    heading: str | None = None
    level: int = Field(default=1, ge=1, le=4)
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    callout: str | None = None
    table: TableSpec | None = None
    image_url: str | None = None
    image_base64: str | None = None
    image_caption: str | None = None


class DocumentSpec(BaseModel):
    """Complete specification for a professional Word document."""

    title: str = Field(min_length=1, max_length=255)
    subtitle: str | None = None
    author: str | None = None
    theme: Literal["slate", "navy", "emerald", "crimson", "dark"] = "slate"
    sections: list[DocumentSectionSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Presentation / PPTX Schemas
# ---------------------------------------------------------------------------
SlideLayout = Literal[
    "title",
    "bullets",
    "cards",
    "comparison",
    "quote",
    "image_left",
    "image_right",
    "full_image",
    "chart",
    "timeline",
]


class StatCard(BaseModel):
    """Highlight metric or stat card on a presentation slide."""

    title: str
    value: str
    description: str | None = None


class ChartSeries(BaseModel):
    """One data series in a native presentation chart."""

    name: str
    values: list[float | int] = Field(default_factory=list)


class TimelineStep(BaseModel):
    """One chronological milestone step in a presentation timeline."""

    step: str
    title: str
    description: str | None = None


class SlideSpec(BaseModel):
    """Specification for a single presentation slide."""

    title: str
    subtitle: str | None = None
    layout: SlideLayout = "bullets"
    bullets: list[str] = Field(default_factory=list)
    cards: list[StatCard] = Field(default_factory=list)
    left_column: list[str] = Field(default_factory=list)
    right_column: list[str] = Field(default_factory=list)
    quote: str | None = None
    author: str | None = None
    image_url: str | None = None
    image_base64: str | None = None
    image_caption: str | None = None
    chart_type: Literal["column", "bar", "line", "pie"] | None = None
    chart_categories: list[str] = Field(default_factory=list)
    chart_series: list[ChartSeries] = Field(default_factory=list)
    timeline_steps: list[TimelineStep] = Field(default_factory=list)


class PresentationSpec(BaseModel):
    """Complete specification for a PowerPoint presentation deck."""

    title: str = Field(min_length=1, max_length=255)
    subtitle: str | None = None
    theme: Literal["slate", "navy", "emerald", "crimson", "dark"] = "slate"
    slides: list[SlideSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Generic Create & Response Schemas
# ---------------------------------------------------------------------------
ArtifactFormat = Literal["xlsx", "docx", "pptx", "pdf", "markdown"]


class CreateArtifactRequest(BaseModel):
    """API request payload to generate or upload an artifact."""

    native_user_id: str
    title: str
    artifact_type: ArtifactFormat
    workbook_spec: WorkbookSpec | None = None
    document_spec: DocumentSpec | None = None
    presentation_spec: PresentationSpec | None = None
    raw_content: str | None = None
    change_summary: str = "Initial creation"


class ReviseArtifactRequest(BaseModel):
    """API request payload to update an existing artifact to version N+1."""

    native_user_id: str
    workbook_spec: WorkbookSpec | None = None
    document_spec: DocumentSpec | None = None
    presentation_spec: PresentationSpec | None = None
    raw_content: str | None = None
    change_summary: str = "AI Revision"


class ArtifactVersionResponse(BaseModel):
    """Metadata response for one version in an artifact chain."""

    version_num: int
    content_sha256: str
    file_size_bytes: int
    mime_type: str
    change_summary: str
    created_at: str


class ArtifactResponse(BaseModel):
    """Full metadata response for an artifact."""

    id: str
    native_user_id: str
    title: str
    slug: str
    artifact_type: str
    current_version_num: int
    created_at: str
    updated_at: str
    versions: list[ArtifactVersionResponse] = Field(default_factory=list)
    download_url: str
    base64_data: str | None = None
    mime_type: str | None = None
    onlyoffice_url: str | None = None
