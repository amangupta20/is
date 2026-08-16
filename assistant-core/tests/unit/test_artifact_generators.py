"""Unit tests for XLSX, DOCX, and PPTX artifact generators."""

import io

import openpyxl
from docx import Document
from pptx import Presentation

from assistant_core.artifacts.generators.docx_gen import DocxGenerator
from assistant_core.artifacts.generators.pptx_gen import PptxGenerator
from assistant_core.artifacts.generators.xlsx_gen import XlsxGenerator
from assistant_core.artifacts.schemas import (
    DocumentSectionSpec,
    DocumentSpec,
    PresentationSpec,
    SheetSpec,
    SlideSpec,
    StatCard,
    TableSpec,
    WorkbookSpec,
)


def test_xlsx_generator_creates_valid_workbook() -> None:
    spec = WorkbookSpec(
        title="Q3 Revenue",
        sheets=[
            SheetSpec(
                name="Summary",
                headers=["Department", "Q1", "Q2", "Q3"],
                rows=[
                    ["Engineering", 120000, 135000, 142000],
                    ["Marketing", 45000, 52000, 48000],
                    ["Operations", 30000, 31000, 33000],
                ],
                column_types=["text", "currency", "currency", "currency"],
                totals_row=True,
            )
        ],
    )
    data = XlsxGenerator.generate(spec)
    assert isinstance(data, bytes)
    assert len(data) > 0

    # Load back with openpyxl to verify integrity
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert "Summary" in wb.sheetnames
    ws = wb["Summary"]
    assert ws["A1"].value == "Department"
    assert ws["B2"].value == 120000
    assert ws["A5"].value == "Total"
    assert "=SUM(B2:B4)" in str(ws["B5"].value)


def test_docx_generator_creates_valid_document() -> None:
    spec = DocumentSpec(
        title="Architecture Decision Record: Artifact Management",
        subtitle="Durable storage and Office document rendering",
        author="Lead Architect",
        sections=[
            DocumentSectionSpec(
                heading="Context & Problem Statement",
                level=1,
                paragraphs=[
                    "The system requires durable storage for generated documents.",
                    "Files must be editable in OnlyOffice without losing version history.",
                ],
                bullets=[
                    "Sub-millisecond local volume reads",
                    "Immutable version chain",
                ],
                callout="All volumes are backed up to S3 in daily scheduled runs.",
                table=TableSpec(
                    headers=["Format", "Engine", "MIME Type"],
                    rows=[
                        ["XLSX", "openpyxl", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
                        ["DOCX", "python-docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
                    ],
                ),
            )
        ],
    )
    data = DocxGenerator.generate(spec)
    assert isinstance(data, bytes)
    assert len(data) > 0

    # Load back with python-docx
    doc = Document(io.BytesIO(data))
    paragraphs_text = [p.text for p in doc.paragraphs if p.text]
    assert any("Architecture Decision Record" in t for t in paragraphs_text)
    assert any("Context & Problem Statement" in t for t in paragraphs_text)
    assert len(doc.tables) >= 2  # 1 callout table + 1 data table


def test_pptx_generator_creates_valid_presentation() -> None:
    spec = PresentationSpec(
        title="Homelab Modernization",
        subtitle="Portainer to Komodo Migration",
        slides=[
            SlideSpec(
                title="Migration Milestones",
                layout="bullets",
                bullets=[
                    "Phase 1: Isolated Open Terminal VM setup",
                    "Phase 2: GitOps stacks repository setup",
                    "Phase 3: Komodo REST API integration",
                ],
            ),
            SlideSpec(
                title="Target Metrics",
                layout="cards",
                cards=[
                    StatCard(title="Uptime", value="99.99%", description="Zero downtime deployment"),
                    StatCard(title="Memory Saved", value="4.2 GB", description="Reduced background overhead"),
                ],
            ),
            SlideSpec(
                title="Architecture Comparison",
                layout="comparison",
                left_column=["Portainer Web UI", "Manual Stack Edits", "Local State"],
                right_column=["Komodo Orchestrator", "Declarative GitOps", "Immutable Versions"],
            ),
            SlideSpec(
                title="Guiding Principle",
                layout="quote",
                quote="Automate everything, verify continuously, recover instantly.",
                author="SRE Handbook",
            ),
        ],
    )
    data = PptxGenerator.generate(spec)
    assert isinstance(data, bytes)
    assert len(data) > 0

    # Load back with python-pptx
    prs = Presentation(io.BytesIO(data))
    assert len(prs.slides) == 5  # 1 Title + 4 Content slides
