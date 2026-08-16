import base64
import io

import httpx
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from assistant_core.artifacts.schemas import PresentationSpec, SlideSpec

# Theme palettes
THEMES: dict[str, dict[str, RGBColor]] = {
    "slate": {
        "dark_bg": RGBColor(15, 23, 42),      # Slate 900
        "light_bg": RGBColor(248, 250, 252),  # Slate 50
        "card_bg": RGBColor(241, 245, 249),   # Slate 100
        "card_border": RGBColor(203, 213, 225), # Slate 300
        "primary_text": RGBColor(15, 23, 42), # Slate 900
        "secondary_text": RGBColor(71, 85, 105), # Slate 600
        "muted_text": RGBColor(148, 163, 184), # Slate 400
        "accent": RGBColor(59, 130, 246),     # Blue 500
        "white": RGBColor(255, 255, 255),
    },
    "navy": {
        "dark_bg": RGBColor(11, 25, 44),
        "light_bg": RGBColor(245, 247, 250),
        "card_bg": RGBColor(238, 242, 246),
        "card_border": RGBColor(197, 208, 223),
        "primary_text": RGBColor(11, 25, 44),
        "secondary_text": RGBColor(65, 90, 119),
        "muted_text": RGBColor(119, 141, 169),
        "accent": RGBColor(0, 141, 218),
        "white": RGBColor(255, 255, 255),
    },
    "emerald": {
        "dark_bg": RGBColor(6, 78, 59),
        "light_bg": RGBColor(240, 253, 244),
        "card_bg": RGBColor(236, 253, 245),
        "card_border": RGBColor(167, 243, 208),
        "primary_text": RGBColor(6, 78, 59),
        "secondary_text": RGBColor(22, 101, 52),
        "muted_text": RGBColor(110, 160, 130),
        "accent": RGBColor(16, 185, 129),
        "white": RGBColor(255, 255, 255),
    },
    "crimson": {
        "dark_bg": RGBColor(76, 5, 25),
        "light_bg": RGBColor(255, 241, 242),
        "card_bg": RGBColor(255, 228, 230),
        "card_border": RGBColor(254, 205, 211),
        "primary_text": RGBColor(76, 5, 25),
        "secondary_text": RGBColor(159, 18, 57),
        "muted_text": RGBColor(190, 110, 130),
        "accent": RGBColor(244, 63, 94),
        "white": RGBColor(255, 255, 255),
    },
    "dark": {
        "dark_bg": RGBColor(10, 15, 26),
        "light_bg": RGBColor(15, 23, 42),
        "card_bg": RGBColor(30, 41, 59),
        "card_border": RGBColor(51, 65, 85),
        "primary_text": RGBColor(248, 250, 252),
        "secondary_text": RGBColor(203, 213, 225),
        "muted_text": RGBColor(148, 163, 184),
        "accent": RGBColor(96, 165, 250),
        "white": RGBColor(255, 255, 255),
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


class PptxGenerator:
    """Renders structured PresentationSpec into polished 16:9 .pptx bytes."""

    @staticmethod
    def generate(spec: PresentationSpec) -> bytes:
        prs = Presentation()
        # Set 16:9 Widescreen dimensions
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        blank_slide_layout = prs.slide_layouts[6]  # Blank slide

        theme_colors = THEMES.get(spec.theme, THEMES["slate"])

        # 1. Title Slide
        title_slide = prs.slides.add_slide(blank_slide_layout)
        PptxGenerator._render_title_slide(title_slide, spec.title, spec.subtitle, theme_colors)

        # 2. Content Slides
        for s_idx, slide_spec in enumerate(spec.slides):
            slide = prs.slides.add_slide(blank_slide_layout)
            PptxGenerator._render_header(
                slide, slide_spec.title, slide_spec.subtitle, slide_num=s_idx + 2, theme=theme_colors
            )

            layout = slide_spec.layout
            if layout == "cards" and slide_spec.cards:
                PptxGenerator._render_cards_layout(slide, slide_spec, theme_colors)
            elif layout == "comparison" and (slide_spec.left_column or slide_spec.right_column):
                PptxGenerator._render_comparison_layout(slide, slide_spec, theme_colors)
            elif layout == "quote" and slide_spec.quote:
                PptxGenerator._render_quote_layout(slide, slide_spec, theme_colors)
            elif layout in ("image_left", "image_right"):
                PptxGenerator._render_image_split_layout(
                    slide, slide_spec, theme_colors, image_on_left=(layout == "image_left")
                )
            elif layout == "full_image":
                PptxGenerator._render_full_image_layout(slide, slide_spec, theme_colors)
            elif layout == "chart" and slide_spec.chart_type:
                PptxGenerator._render_chart_layout(slide, slide_spec, theme_colors)
            elif layout == "timeline" and slide_spec.timeline_steps:
                PptxGenerator._render_timeline_layout(slide, slide_spec, theme_colors)
            else:
                PptxGenerator._render_bullets_layout(slide, slide_spec, theme_colors)

        output = io.BytesIO()
        prs.save(output)
        return output.getvalue()

    @staticmethod
    def _render_title_slide(
        slide: object, title: str, subtitle: str | None, theme: dict[str, RGBColor]
    ) -> None:
        """Render a full-bleed title slide."""
        bg = slide.shapes.add_shape(  # type: ignore[attr-defined]
            MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5)
        )
        bg.fill.solid()
        bg.fill.fore_color.rgb = theme["dark_bg"]
        bg.line.color.rgb = theme["dark_bg"]

        tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
            Inches(1.2), Inches(2.2), Inches(11.0), Inches(3.2)
        )
        tf = tb.text_frame
        tf.word_wrap = True

        p_title = tf.paragraphs[0]
        p_title.text = title
        p_title.font.name = "Calibri"
        p_title.font.size = Pt(40)
        p_title.font.bold = True
        p_title.font.color.rgb = theme["white"]

        if subtitle:
            p_sub = tf.add_paragraph()
            p_sub.text = subtitle
            p_sub.font.name = "Calibri"
            p_sub.font.size = Pt(20)
            p_sub.font.color.rgb = theme["muted_text"]
            p_sub.space_before = Pt(14)

    @staticmethod
    def _render_header(
        slide: object,
        title: str,
        subtitle: str | None,
        slide_num: int,
        theme: dict[str, RGBColor],
    ) -> None:
        """Render standard top header and slide numbering."""
        tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
            Inches(0.8), Inches(0.6), Inches(11.5), Inches(1.2)
        )
        tf = tb.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = "Calibri"
        p.font.size = Pt(26)
        p.font.bold = True
        p.font.color.rgb = theme["primary_text"]

        if subtitle:
            p_sub = tf.add_paragraph()
            p_sub.text = subtitle
            p_sub.font.name = "Calibri"
            p_sub.font.size = Pt(13)
            p_sub.font.color.rgb = theme["secondary_text"]

        num_tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
            Inches(12.0), Inches(6.8), Inches(0.8), Inches(0.4)
        )
        num_tf = num_tb.text_frame
        p_num = num_tf.paragraphs[0]
        p_num.text = str(slide_num)
        p_num.font.name = "Calibri"
        p_num.font.size = Pt(11)
        p_num.font.color.rgb = theme["muted_text"]
        p_num.alignment = PP_ALIGN.RIGHT

    @staticmethod
    def _render_bullets_layout(
        slide: object, slide_spec: SlideSpec, theme: dict[str, RGBColor]
    ) -> None:
        """Render bulleted points with clear spacing."""
        tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
            Inches(0.8), Inches(1.8), Inches(11.5), Inches(4.8)
        )
        tf = tb.text_frame
        tf.word_wrap = True

        for idx, bullet in enumerate(slide_spec.bullets):
            p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
            p.text = f"•  {bullet}"
            p.font.name = "Calibri"
            p.font.size = Pt(16)
            p.font.color.rgb = theme["primary_text"]
            p.space_after = Pt(14)

    @staticmethod
    def _render_cards_layout(
        slide: object, slide_spec: SlideSpec, theme: dict[str, RGBColor]
    ) -> None:
        """Render 2, 3, or 4 visual metric/stat cards."""
        cards = slide_spec.cards
        count = min(len(cards), 4)
        if count == 0:
            return

        total_width = 11.5
        spacing = 0.3
        card_width = (total_width - (spacing * (count - 1))) / count
        card_height = 4.2
        top = 2.0
        left_start = 0.8

        for idx, card in enumerate(cards[:count]):
            c_left = left_start + idx * (card_width + spacing)
            box = slide.shapes.add_shape(  # type: ignore[attr-defined]
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(c_left),
                Inches(top),
                Inches(card_width),
                Inches(card_height),
            )
            box.fill.solid()
            box.fill.fore_color.rgb = theme["card_bg"]
            box.line.color.rgb = theme["card_border"]
            box.line.width = Pt(1)

            tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
                Inches(c_left + 0.2),
                Inches(top + 0.3),
                Inches(card_width - 0.4),
                Inches(card_height - 0.6),
            )
            tf = tb.text_frame
            tf.word_wrap = True

            p1 = tf.paragraphs[0]
            p1.text = card.title
            p1.font.name = "Calibri"
            p1.font.size = Pt(13)
            p1.font.bold = True
            p1.font.color.rgb = theme["secondary_text"]

            p2 = tf.add_paragraph()
            p2.text = card.value
            p2.font.name = "Calibri"
            p2.font.size = Pt(28)
            p2.font.bold = True
            p2.font.color.rgb = theme["accent"]
            p2.space_before = Pt(8)

            if card.description:
                p3 = tf.add_paragraph()
                p3.text = card.description
                p3.font.name = "Calibri"
                p3.font.size = Pt(11.5)
                p3.font.color.rgb = theme["primary_text"]
                p3.space_before = Pt(8)

    @staticmethod
    def _render_comparison_layout(
        slide: object, slide_spec: SlideSpec, theme: dict[str, RGBColor]
    ) -> None:
        """Render 2-column side-by-side comparison boxes."""
        col_w = Inches(5.6)
        col_h = Inches(4.5)
        top = Inches(1.8)

        # Left Column
        left_box = slide.shapes.add_shape(  # type: ignore[attr-defined]
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), top, col_w, col_h
        )
        left_box.fill.solid()
        left_box.fill.fore_color.rgb = theme["card_bg"]
        left_box.line.color.rgb = theme["card_border"]

        ltb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
            Inches(1.0), Inches(2.0), Inches(5.2), Inches(4.0)
        )
        ltf = ltb.text_frame
        ltf.word_wrap = True
        for idx, item in enumerate(slide_spec.left_column):
            p = ltf.paragraphs[0] if idx == 0 else ltf.add_paragraph()
            p.text = f"• {item}"
            p.font.name = "Calibri"
            p.font.size = Pt(14)
            p.font.color.rgb = theme["primary_text"]
            p.space_after = Pt(10)

        # Right Column
        right_box = slide.shapes.add_shape(  # type: ignore[attr-defined]
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(6.9), top, col_w, col_h
        )
        right_box.fill.solid()
        right_box.fill.fore_color.rgb = theme["card_bg"]
        right_box.line.color.rgb = theme["card_border"]

        rtb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
            Inches(7.1), Inches(2.0), Inches(5.2), Inches(4.0)
        )
        rtf = rtb.text_frame
        rtf.word_wrap = True
        for idx, item in enumerate(slide_spec.right_column):
            p = rtf.paragraphs[0] if idx == 0 else rtf.add_paragraph()
            p.text = f"• {item}"
            p.font.name = "Calibri"
            p.font.size = Pt(14)
            p.font.color.rgb = theme["primary_text"]
            p.space_after = Pt(10)

    @staticmethod
    def _render_quote_layout(
        slide: object, slide_spec: SlideSpec, theme: dict[str, RGBColor]
    ) -> None:
        """Render a large highlight quote slide."""
        tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
            Inches(1.5), Inches(2.5), Inches(10.3), Inches(3.5)
        )
        tf = tb.text_frame
        tf.word_wrap = True

        p = tf.paragraphs[0]
        p.text = f"“{slide_spec.quote}”"
        p.font.name = "Calibri"
        p.font.size = Pt(28)
        p.font.italic = True
        p.font.color.rgb = theme["primary_text"]

        if slide_spec.author:
            p_auth = tf.add_paragraph()
            p_auth.text = f"— {slide_spec.author}"
            p_auth.font.name = "Calibri"
            p_auth.font.size = Pt(16)
            p_auth.font.bold = True
            p_auth.font.color.rgb = theme["accent"]
            p_auth.space_before = Pt(14)

    @staticmethod
    def _render_image_split_layout(
        slide: object,
        slide_spec: SlideSpec,
        theme: dict[str, RGBColor],
        image_on_left: bool = False,
    ) -> None:
        """Render split 50/50 layout with text and an image."""
        text_left = Inches(6.8) if image_on_left else Inches(0.8)
        image_left = Inches(0.8) if image_on_left else Inches(6.8)

        # 1. Text Section
        tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
            text_left, Inches(1.8), Inches(5.6), Inches(4.8)
        )
        tf = tb.text_frame
        tf.word_wrap = True

        for idx, bullet in enumerate(slide_spec.bullets):
            p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
            p.text = f"•  {bullet}"
            p.font.name = "Calibri"
            p.font.size = Pt(15)
            p.font.color.rgb = theme["primary_text"]
            p.space_after = Pt(12)

        # 2. Image Section
        img_stream = _resolve_image_stream(slide_spec.image_url, slide_spec.image_base64)
        if img_stream:
            try:
                slide.shapes.add_picture(  # type: ignore[attr-defined]
                    img_stream, image_left, Inches(1.8), width=Inches(5.7), height=Inches(4.6)
                )
            except Exception:  # noqa: BLE001
                img_stream = None

        if not img_stream:
            # Placeholder box if image is unavailable
            box = slide.shapes.add_shape(  # type: ignore[attr-defined]
                MSO_SHAPE.ROUNDED_RECTANGLE, image_left, Inches(1.8), Inches(5.7), Inches(4.6)
            )
            box.fill.solid()
            box.fill.fore_color.rgb = theme["card_bg"]
            box.line.color.rgb = theme["card_border"]

            ph_tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
                image_left, Inches(3.6), Inches(5.7), Inches(1.0)
            )
            ph_p = ph_tb.text_frame.paragraphs[0]
            ph_p.text = "🖼️ Image Preview"
            ph_p.alignment = PP_ALIGN.CENTER
            ph_p.font.color.rgb = theme["muted_text"]

    @staticmethod
    def _render_full_image_layout(
        slide: object, slide_spec: SlideSpec, theme: dict[str, RGBColor]
    ) -> None:
        """Render a large centered image layout."""
        img_stream = _resolve_image_stream(slide_spec.image_url, slide_spec.image_base64)
        if img_stream:
            try:
                slide.shapes.add_picture(  # type: ignore[attr-defined]
                    img_stream, Inches(1.5), Inches(1.8), width=Inches(10.3), height=Inches(4.6)
                )
            except Exception:  # noqa: BLE001
                img_stream = None

        if not img_stream:
            box = slide.shapes.add_shape(  # type: ignore[attr-defined]
                MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1.5), Inches(1.8), Inches(10.3), Inches(4.6)
            )
            box.fill.solid()
            box.fill.fore_color.rgb = theme["card_bg"]
            box.line.color.rgb = theme["card_border"]

        if slide_spec.image_caption:
            cap_tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
                Inches(1.5), Inches(6.5), Inches(10.3), Inches(0.5)
            )
            cap_p = cap_tb.text_frame.paragraphs[0]
            cap_p.text = slide_spec.image_caption
            cap_p.alignment = PP_ALIGN.CENTER
            cap_p.font.italic = True
            cap_p.font.size = Pt(12)
            cap_p.font.color.rgb = theme["secondary_text"]

    @staticmethod
    def _render_chart_layout(
        slide: object, slide_spec: SlideSpec, theme: dict[str, RGBColor]
    ) -> None:
        """Render native PowerPoint XML charts (Column, Bar, Line, Pie)."""
        chart_type_map = {
            "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "bar": XL_CHART_TYPE.BAR_CLUSTERED,
            "line": XL_CHART_TYPE.LINE,
            "pie": XL_CHART_TYPE.PIE,
        }
        xl_type = chart_type_map.get(slide_spec.chart_type or "column", XL_CHART_TYPE.COLUMN_CLUSTERED)

        chart_data = CategoryChartData()
        categories = slide_spec.chart_categories or ["Category 1", "Category 2", "Category 3"]
        chart_data.categories = categories

        if slide_spec.chart_series:
            for s in slide_spec.chart_series:
                chart_data.add_series(s.name, s.values)
        else:
            chart_data.add_series("Series 1", [10, 25, 40][: len(categories)])

        x, y, cx, cy = Inches(1.2), Inches(1.8), Inches(10.9), Inches(4.8)
        slide.shapes.add_chart(xl_type, x, y, cx, cy, chart_data)  # type: ignore[attr-defined]

    @staticmethod
    def _render_timeline_layout(
        slide: object, slide_spec: SlideSpec, theme: dict[str, RGBColor]
    ) -> None:
        """Render sequential milestone cards across the slide."""
        steps = slide_spec.timeline_steps
        count = min(len(steps), 5)
        if count == 0:
            return

        total_width = 11.5
        spacing = 0.25
        card_width = (total_width - (spacing * (count - 1))) / count
        card_height = 4.2
        top = 2.0
        left_start = 0.8

        for idx, step in enumerate(steps[:count]):
            c_left = left_start + idx * (card_width + spacing)
            box = slide.shapes.add_shape(  # type: ignore[attr-defined]
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(c_left),
                Inches(top),
                Inches(card_width),
                Inches(card_height),
            )
            box.fill.solid()
            box.fill.fore_color.rgb = theme["card_bg"]
            box.line.color.rgb = theme["card_border"]

            tb = slide.shapes.add_textbox(  # type: ignore[attr-defined]
                Inches(c_left + 0.15),
                Inches(top + 0.25),
                Inches(card_width - 0.3),
                Inches(card_height - 0.5),
            )
            tf = tb.text_frame
            tf.word_wrap = True

            # Step Pill (e.g. Phase 1)
            p1 = tf.paragraphs[0]
            p1.text = step.step.upper()
            p1.font.name = "Calibri"
            p1.font.size = Pt(11)
            p1.font.bold = True
            p1.font.color.rgb = theme["accent"]

            # Step Title
            p2 = tf.add_paragraph()
            p2.text = step.title
            p2.font.name = "Calibri"
            p2.font.size = Pt(14)
            p2.font.bold = True
            p2.font.color.rgb = theme["primary_text"]
            p2.space_before = Pt(6)

            # Step Description
            if step.description:
                p3 = tf.add_paragraph()
                p3.text = step.description
                p3.font.name = "Calibri"
                p3.font.size = Pt(11)
                p3.font.color.rgb = theme["secondary_text"]
                p3.space_before = Pt(8)
