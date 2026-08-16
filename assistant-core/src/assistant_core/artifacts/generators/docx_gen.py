"""Professional Word document (.docx) generator using python-docx."""

import io
from typing import Any

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt, RGBColor

from assistant_core.artifacts.schemas import DocumentSpec, TableSpec

# Brand colors
PRIMARY_COLOR = RGBColor(30, 41, 59)  # Slate 800
SECONDARY_COLOR = RGBColor(71, 85, 105)  # Slate 600
MUTED_COLOR = RGBColor(100, 116, 139)  # Slate 500
ACCENT_HEX = "3B82F6"  # Blue 500
HEADER_BG_HEX = "1E293B"  # Slate 800
ZEBRA_BG_HEX = "F8FAFC"  # Slate 50
CALLOUT_BG_HEX = "F1F5F9"  # Slate 100


def _set_cell_background(cell: object, hex_color: str) -> None:
    """Set background color of a table cell."""
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    cell._tc.get_or_add_tcPr().append(shading)  # type: ignore[attr-defined]


def _set_cell_margins(cell: object, top: int = 120, bottom: int = 120, left: int = 160, right: int = 160) -> None:
    """Set inner padding for a table cell in twentieths of a point (dxa)."""
    tcPr = cell._tc.get_or_add_tcPr()  # type: ignore[attr-defined]
    tcMar = OxmlElement("w:tcMar")
    for side, val in [("top", top), ("bottom", bottom), ("left", left), ("right", right)]:
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        tcMar.append(node)
    tcPr.append(tcMar)


class DocxGenerator:
    """Renders structured DocumentSpec into polished .docx bytes."""

    @staticmethod
    def generate(spec: DocumentSpec) -> bytes:
        doc = Document()

        # Set standard margins (1 inch)
        for section in doc.sections:
            section.top_margin = Inches(1.0)
            section.bottom_margin = Inches(1.0)
            section.left_margin = Inches(1.0)
            section.right_margin = Inches(1.0)

        # 1. Document Title
        title_p = doc.add_paragraph()
        title_p.paragraph_format.space_before = Pt(0)
        title_p.paragraph_format.space_after = Pt(4)
        run_title = title_p.add_run(spec.title)
        run_title.font.name = "Calibri"
        run_title.font.size = Pt(24)
        run_title.font.bold = True
        run_title.font.color.rgb = PRIMARY_COLOR

        # Subtitle / Author
        if spec.subtitle:
            sub_p = doc.add_paragraph()
            sub_p.paragraph_format.space_before = Pt(0)
            sub_p.paragraph_format.space_after = Pt(8)
            run_sub = sub_p.add_run(spec.subtitle)
            run_sub.font.name = "Calibri"
            run_sub.font.size = Pt(14)
            run_sub.font.color.rgb = SECONDARY_COLOR

        if spec.author:
            auth_p = doc.add_paragraph()
            auth_p.paragraph_format.space_before = Pt(0)
            auth_p.paragraph_format.space_after = Pt(18)
            run_auth = auth_p.add_run(f"Author: {spec.author}")
            run_auth.font.name = "Calibri"
            run_auth.font.size = Pt(10)
            run_auth.font.italic = True
            run_auth.font.color.rgb = MUTED_COLOR

        # 2. Sections
        for sec in spec.sections:
            # Heading
            if sec.heading:
                h_p = doc.add_paragraph()
                h_p.paragraph_format.keep_with_next = True
                h_p.paragraph_format.space_before = Pt(16)
                h_p.paragraph_format.space_after = Pt(6)

                h_run = h_p.add_run(sec.heading)
                h_run.font.name = "Calibri"
                h_run.font.bold = True

                if sec.level == 1:
                    h_run.font.size = Pt(16)
                    h_run.font.color.rgb = PRIMARY_COLOR
                elif sec.level == 2:
                    h_run.font.size = Pt(13.5)
                    h_run.font.color.rgb = SECONDARY_COLOR
                else:
                    h_run.font.size = Pt(12)
                    h_run.font.color.rgb = SECONDARY_COLOR

            # Paragraphs
            for text in sec.paragraphs:
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(6)
                p.paragraph_format.line_spacing = 1.15
                r = p.add_run(text)
                r.font.name = "Calibri"
                r.font.size = Pt(11)
                r.font.color.rgb = PRIMARY_COLOR

            # Bullet points
            for bullet in sec.bullets:
                bp = doc.add_paragraph(style="List Bullet")
                bp.paragraph_format.space_before = Pt(0)
                bp.paragraph_format.space_after = Pt(3)
                r = bp.add_run(bullet)
                r.font.name = "Calibri"
                r.font.size = Pt(11)
                r.font.color.rgb = PRIMARY_COLOR

            # Callout Note
            if sec.callout:
                callout_tbl = doc.add_table(rows=1, cols=1)
                callout_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
                callout_cell = callout_tbl.cell(0, 0)
                _set_cell_background(callout_cell, CALLOUT_BG_HEX)
                _set_cell_margins(callout_cell, top=140, bottom=140, left=200, right=160)

                # Set left border accent
                tcPr = callout_cell._tc.get_or_add_tcPr()
                tcBorders = parse_xml(
                    f'<w:tcBorders {nsdecls("w")}>'
                    f'<w:top w:val="none"/>'
                    f'<w:left w:val="single" w:sz="24" w:space="0" w:color="{ACCENT_HEX}"/>'
                    f'<w:bottom w:val="none"/>'
                    f'<w:right w:val="none"/>'
                    f'</w:tcBorders>'
                )
                tcPr.append(tcBorders)

                cp = callout_cell.paragraphs[0]
                cp.paragraph_format.space_before = Pt(0)
                cp.paragraph_format.space_after = Pt(0)
                cr = cp.add_run(f"💡 {sec.callout}")
                cr.font.name = "Calibri"
                cr.font.size = Pt(10.5)
                cr.font.color.rgb = PRIMARY_COLOR

                # Spacer paragraph after table
                spacer = doc.add_paragraph()
                spacer.paragraph_format.space_before = Pt(6)
                spacer.paragraph_format.space_after = Pt(0)

            # Embedded Table
            if sec.table and sec.table.headers:
                DocxGenerator._render_table(doc, sec.table)

        output = io.BytesIO()
        doc.save(output)
        return output.getvalue()

    @staticmethod
    def _render_table(doc: Any, tbl_spec: TableSpec) -> None:
        """Render a styled data table inside the Word document."""
        num_rows = len(tbl_spec.rows) + 1
        num_cols = len(tbl_spec.headers)
        tbl = doc.add_table(rows=num_rows, cols=num_cols)
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

        # 1. Header Row
        for c_idx, h_text in enumerate(tbl_spec.headers):
            cell = tbl.cell(0, c_idx)
            _set_cell_background(cell, HEADER_BG_HEX)
            _set_cell_margins(cell, top=140, bottom=140, left=140, right=140)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            r = p.add_run(h_text)
            r.font.name = "Calibri"
            r.font.size = Pt(10.5)
            r.font.bold = True
            r.font.color.rgb = RGBColor(255, 255, 255)

        # 2. Data Rows
        for r_idx, row_data in enumerate(tbl_spec.rows):
            fill_hex = ZEBRA_BG_HEX if r_idx % 2 == 1 else "FFFFFF"
            for c_idx in range(num_cols):
                cell = tbl.cell(r_idx + 1, c_idx)
                _set_cell_background(cell, fill_hex)
                _set_cell_margins(cell, top=100, bottom=100, left=140, right=140)
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

                val_text = str(row_data[c_idx]) if c_idx < len(row_data) else ""
                p = cell.paragraphs[0]
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                r = p.add_run(val_text)
                r.font.name = "Calibri"
                r.font.size = Pt(10)
                r.font.color.rgb = PRIMARY_COLOR

        spacer = doc.add_paragraph()
        spacer.paragraph_format.space_before = Pt(8)
        spacer.paragraph_format.space_after = Pt(0)
