"""Professional PDF document generator using ReportLab."""

import base64
import html
import io
from typing import Any

import httpx
from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import (
    Image as RLImage,
)

from assistant_core.artifacts.schemas import DocumentSpec, TableSpec

# Theme palettes matching DOCX and presentation generators
PDF_THEMES: dict[str, dict[str, HexColor]] = {
    "slate": {
        "primary": HexColor("#1E293B"),
        "secondary": HexColor("#475569"),
        "muted": HexColor("#64748B"),
        "accent": HexColor("#3B82F6"),
        "header_bg": HexColor("#1E293B"),
        "header_text": HexColor("#FFFFFF"),
        "zebra_bg": HexColor("#F8FAFC"),
        "callout_bg": HexColor("#F1F5F9"),
        "callout_border": HexColor("#3B82F6"),
    },
    "navy": {
        "primary": HexColor("#0B192C"),
        "secondary": HexColor("#415A77"),
        "muted": HexColor("#778DA9"),
        "accent": HexColor("#008DDA"),
        "header_bg": HexColor("#0B192C"),
        "header_text": HexColor("#FFFFFF"),
        "zebra_bg": HexColor("#F5F7FA"),
        "callout_bg": HexColor("#EEF2F6"),
        "callout_border": HexColor("#008DDA"),
    },
    "emerald": {
        "primary": HexColor("#064E3B"),
        "secondary": HexColor("#166534"),
        "muted": HexColor("#6EA082"),
        "accent": HexColor("#10B981"),
        "header_bg": HexColor("#064E3B"),
        "header_text": HexColor("#FFFFFF"),
        "zebra_bg": HexColor("#F0FDF4"),
        "callout_bg": HexColor("#ECFDF5"),
        "callout_border": HexColor("#10B981"),
    },
    "crimson": {
        "primary": HexColor("#4C0519"),
        "secondary": HexColor("#9F1239"),
        "muted": HexColor("#BE6E82"),
        "accent": HexColor("#F43F5E"),
        "header_bg": HexColor("#4C0519"),
        "header_text": HexColor("#FFFFFF"),
        "zebra_bg": HexColor("#FFF1F2"),
        "callout_bg": HexColor("#FFE4E6"),
        "callout_border": HexColor("#F43F5E"),
    },
    "dark": {
        "primary": HexColor("#0F172A"),
        "secondary": HexColor("#334155"),
        "muted": HexColor("#64748B"),
        "accent": HexColor("#60A5FA"),
        "header_bg": HexColor("#0F172A"),
        "header_text": HexColor("#FFFFFF"),
        "zebra_bg": HexColor("#F1F5F9"),
        "callout_bg": HexColor("#E2E8F0"),
        "callout_border": HexColor("#60A5FA"),
    },
}


def _resolve_image_stream(image_url: str | None, image_base64: str | None) -> io.BytesIO | None:
    """Fetch and validate an image buffer from URL or base64 data."""
    raw_bytes: bytes | None = None

    if image_base64:
        try:
            b64_str = image_base64
            if "," in b64_str:
                b64_str = b64_str.split(",", 1)[1]
            b64_padded = b64_str + "=" * (-len(b64_str) % 4)
            raw_bytes = base64.b64decode(b64_padded)
        except Exception:  # noqa: BLE001
            raw_bytes = None

    elif image_url and image_url.startswith(("http://", "https://")):
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                res = client.get(image_url)
                if res.status_code == 200:
                    raw_bytes = res.content
        except Exception:  # noqa: BLE001
            raw_bytes = None

    if not raw_bytes:
        return None

    try:
        buf = io.BytesIO(raw_bytes)
        img = Image.open(buf)
        img.verify()
        return io.BytesIO(raw_bytes)
    except Exception:  # noqa: BLE001
        return None


class NumberedCanvas(canvas.Canvas):  # type: ignore[misc]
    """Two-pass canvas to dynamically compute and draw total page numbers and running headers."""

    doc_title: str = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []
        self.doc_title = ""

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def _draw_page_decorations(self, total_pages: int) -> None:
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(HexColor("#64748B"))

        doc_title = self.doc_title

        # Running header on page 2+
        if self._pageNumber > 1:
            if doc_title:
                clean_title = doc_title if len(doc_title) <= 65 else f"{doc_title[:62]}..."
                self.drawString(54, 11 * inch - 36, clean_title)
            self.setStrokeColor(HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(54, 11 * inch - 42, 8.5 * inch - 54, 11 * inch - 42)

        # Running footer on all pages
        footer_text = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(8.5 * inch - 54, 36, footer_text)
        self.drawString(54, 36, "Generated by Assistant Core")
        self.setStrokeColor(HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(54, 46, 8.5 * inch - 54, 46)
        self.restoreState()


class PdfGenerator:
    """Renders structured DocumentSpec into polished, styled PDF bytes."""

    @classmethod
    def generate(cls, spec: DocumentSpec) -> bytes:
        out_buffer = io.BytesIO()
        theme = PDF_THEMES.get(spec.theme, PDF_THEMES["slate"])

        # Page setup: Letter size with 0.75 in (54 pt) margins -> 504 pt printable width
        margin = 54.0
        doc = SimpleDocTemplate(
            out_buffer,
            pagesize=letter,
            leftMargin=margin,
            rightMargin=margin,
            topMargin=margin,
            bottomMargin=margin,
        )

        styles = getSampleStyleSheet()

        # Custom typography styles
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=theme["primary"],
            spaceAfter=4,
        )

        subtitle_style = ParagraphStyle(
            "DocSubtitle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=12,
            leading=16,
            textColor=theme["secondary"],
            spaceAfter=4,
        )

        meta_style = ParagraphStyle(
            "DocMeta",
            parent=styles["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=9,
            leading=13,
            textColor=theme["muted"],
            spaceAfter=12,
        )

        h1_style = ParagraphStyle(
            "DocH1",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=theme["primary"],
            spaceBefore=14,
            spaceAfter=6,
            keepWithNext=True,
        )

        h2_style = ParagraphStyle(
            "DocH2",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=theme["secondary"],
            spaceBefore=10,
            spaceAfter=4,
            keepWithNext=True,
        )

        h3_style = ParagraphStyle(
            "DocH3",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=theme["secondary"],
            spaceBefore=8,
            spaceAfter=3,
            keepWithNext=True,
        )

        h4_style = ParagraphStyle(
            "DocH4",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=theme["muted"],
            spaceBefore=6,
            spaceAfter=2,
            keepWithNext=True,
        )

        body_style = ParagraphStyle(
            "DocBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13.5,
            textColor=HexColor("#1E293B"),
            spaceBefore=0,
            spaceAfter=6,
        )

        bullet_style = ParagraphStyle(
            "DocBullet",
            parent=body_style,
            leftIndent=14,
            firstLineIndent=-10,
            spaceBefore=1,
            spaceAfter=3,
        )

        callout_style = ParagraphStyle(
            "DocCallout",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13.5,
            textColor=HexColor("#1E293B"),
        )

        table_header_style = ParagraphStyle(
            "TableHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=theme["header_text"],
        )

        table_cell_style = ParagraphStyle(
            "TableCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11.5,
            textColor=HexColor("#1E293B"),
        )

        caption_style = ParagraphStyle(
            "DocCaption",
            parent=styles["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11.5,
            textColor=theme["muted"],
            alignment=1,  # Center
            spaceBefore=3,
            spaceAfter=8,
        )

        printable_width = 8.5 * inch - 2 * margin  # 504 pt
        story: list[Any] = []

        # 1. Title Banner
        story.append(Paragraph(html.escape(spec.title), title_style))

        if spec.subtitle:
            story.append(Paragraph(html.escape(spec.subtitle), subtitle_style))

        if spec.author:
            story.append(Paragraph(f"Author: {html.escape(spec.author)}", meta_style))

        story.append(
            HRFlowable(
                width="100%",
                thickness=1.5,
                color=theme["accent"],
                spaceBefore=2,
                spaceAfter=12,
            )
        )

        heading_styles = {
            1: h1_style,
            2: h2_style,
            3: h3_style,
            4: h4_style,
        }

        # 2. Document Sections
        for section in spec.sections:
            if section.heading:
                lvl = min(max(section.level, 1), 4)
                h_style = heading_styles.get(lvl, h1_style)
                story.append(Paragraph(html.escape(section.heading), h_style))

            for para in section.paragraphs:
                if para and para.strip():
                    story.append(Paragraph(html.escape(para), body_style))

            for bullet in section.bullets:
                if bullet and bullet.strip():
                    story.append(Paragraph(f"&bull;&nbsp;{html.escape(bullet)}", bullet_style))

            if section.callout:
                callout_p = Paragraph(html.escape(section.callout), callout_style)
                callout_table = Table(
                    [[callout_p]],
                    colWidths=[printable_width],
                )
                callout_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), theme["callout_bg"]),
                            ("LEFTPADDING", (0, 0), (-1, -1), 12),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                            ("TOPPADDING", (0, 0), (-1, -1), 8),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                            ("LINELEFT", (0, 0), (0, -1), 3.5, theme["callout_border"]),
                            ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#CBD5E1")),
                        ]
                    )
                )
                story.append(Spacer(1, 4))
                story.append(callout_table)
                story.append(Spacer(1, 8))

            if section.image_url or section.image_base64:
                img_stream = _resolve_image_stream(section.image_url, section.image_base64)
                if img_stream:
                    try:
                        pil_img = Image.open(img_stream)
                        orig_w, orig_h = pil_img.size
                        max_w = printable_width
                        max_h = 240.0
                        scale = min(max_w / orig_w, max_h / orig_h, 1.0)
                        draw_w = orig_w * scale
                        draw_h = orig_h * scale

                        img_stream.seek(0)
                        rl_img = RLImage(img_stream, width=draw_w, height=draw_h)
                        rl_img.hAlign = "CENTER"

                        elements: list[Any] = [Spacer(1, 4), rl_img]
                        if section.image_caption:
                            elements.append(
                                Paragraph(html.escape(section.image_caption), caption_style)
                            )
                        else:
                            elements.append(Spacer(1, 6))

                        story.append(KeepTogether(elements))
                    except Exception:  # noqa: BLE001, S110
                        # Ignore malformed or inaccessible image streams
                        pass

            if section.table:
                cls._append_table(
                    story,
                    section.table,
                    theme,
                    printable_width,
                    table_header_style,
                    table_cell_style,
                )

        # Build PDF with custom NumberedCanvas
        def make_canvas(
            filename: Any,
            **kwargs: Any,
        ) -> NumberedCanvas:
            kwargs.setdefault("pagesize", letter)
            c = NumberedCanvas(filename, **kwargs)
            c.doc_title = spec.title
            return c

        doc.build(story, canvasmaker=make_canvas)
        return out_buffer.getvalue()

    @staticmethod
    def _append_table(
        story: list[Any],
        table_spec: TableSpec,
        theme: dict[str, HexColor],
        printable_width: float,
        header_style: ParagraphStyle,
        cell_style: ParagraphStyle,
    ) -> None:
        """Render a styled data table with auto-wrapped cells and zebra striping."""
        if not table_spec.headers and not table_spec.rows:
            return

        num_cols = max(len(table_spec.headers), max((len(r) for r in table_spec.rows), default=1))
        col_width = printable_width / max(num_cols, 1)

        table_data: list[list[Any]] = []

        if table_spec.headers:
            header_row = [Paragraph(html.escape(h), header_style) for h in table_spec.headers]
            while len(header_row) < num_cols:
                header_row.append(Paragraph("", header_style))
            table_data.append(header_row)

        for row in table_spec.rows:
            data_row = [Paragraph(html.escape(str(c)), cell_style) for c in row]
            while len(data_row) < num_cols:
                data_row.append(Paragraph("", cell_style))
            table_data.append(data_row)

        table = Table(
            table_data, colWidths=[col_width] * num_cols, repeatRows=1 if table_spec.headers else 0
        )

        style_cmds: list[Any] = [
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, HexColor("#E2E8F0")),
            ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#CBD5E1")),
        ]

        if table_spec.headers:
            style_cmds.append(("BACKGROUND", (0, 0), (-1, 0), theme["header_bg"]))
            start_row = 1
        else:
            start_row = 0

        for row_idx in range(start_row, len(table_data)):
            if (row_idx - start_row) % 2 == 1:
                style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), theme["zebra_bg"]))

        table.setStyle(TableStyle(style_cmds))
        story.append(Spacer(1, 6))
        story.append(table)
        story.append(Spacer(1, 8))
