"""Typst generation engine for high-fidelity documents, reports, and structured resumes."""

import base64
import tempfile
from pathlib import Path

import typst

from assistant_core.artifacts.schemas import (
    DocumentSpec,
    ResumeSpec,
    TableSpec,
)

THEME_PALETTES: dict[str, dict[str, str]] = {
    "slate": {
        "primary": "#1e293b",
        "accent": "#0ea5e9",
        "bg_callout": "#f8fafc",
        "border": "#cbd5e1",
        "muted": "#64748b",
    },
    "navy": {
        "primary": "#1e3a8a",
        "accent": "#3b82f6",
        "bg_callout": "#eff6ff",
        "border": "#bfdbfe",
        "muted": "#475569",
    },
    "emerald": {
        "primary": "#065f46",
        "accent": "#10b981",
        "bg_callout": "#ecfdf5",
        "border": "#a7f3d0",
        "muted": "#4b5563",
    },
    "crimson": {
        "primary": "#881337",
        "accent": "#f43f5e",
        "bg_callout": "#fff1f2",
        "border": "#fecdd3",
        "muted": "#4b5563",
    },
    "dark": {
        "primary": "#0f172a",
        "accent": "#64748b",
        "bg_callout": "#f1f5f9",
        "border": "#cbd5e1",
        "muted": "#64748b",
    },
}


def _escape_typst(text: str | None) -> str:
    """Escape Typst syntax characters in plain text."""
    if not text:
        return ""
    # Backslash must be escaped first
    s = text.replace("\\", "\\\\")
    for char in ("#", "$", "[", "]", "*", "_", "<", ">", "@", "`"):
        s = s.replace(char, f"\\{char}")
    return s


class TypstGenerator:
    """High-fidelity document and resume generator powered by Typst."""

    @classmethod
    def compile_markup(cls, markup: str) -> bytes:
        """Compile raw Typst markup string into PDF bytes."""
        result: bytes = typst.compile(markup.encode("utf-8"))
        return result

    @classmethod
    def generate_resume(cls, spec: ResumeSpec) -> bytes:
        """Generate a professional PDF resume from structured ResumeSpec."""
        lines: list[str] = []

        # Document setup & styles
        lines.append('#set page(paper: "a4", margin: (x: 1.5cm, top: 1.5cm, bottom: 1.5cm))')
        lines.append(
            '#set text(font: ("Liberation Sans", "DejaVu Sans", "Arial", "Helvetica"), '
            'size: 9.5pt, fill: rgb("#1e293b"))'
        )
        lines.append("#set par(justify: false, leading: 0.6em)")
        lines.append("")
        lines.append("#let section_heading(title) = {")
        lines.append("  v(7pt)")
        lines.append('  text(size: 11pt, weight: "bold", fill: rgb("#0f172a"))[#upper(title)]')
        lines.append("  v(-4pt)")
        lines.append('  line(length: 100%, stroke: 1.2pt + rgb("#2563eb"))')
        lines.append("  v(3pt)")
        lines.append("}")
        lines.append("")

        # Header / Contact Banner
        lines.append("#align(center)[")
        lines.append(
            f'  #text(size: 20pt, weight: "bold", fill: rgb("#0f172a"))[{_escape_typst(spec.name)}] \\'
        )
        if spec.title:
            lines.append(
                f'  #v(1pt)\n  #text(size: 11pt, fill: rgb("#475569"), weight: "medium")'
                f"[{_escape_typst(spec.title)}] \\"
            )

        contact_parts: list[str] = []
        if spec.email:
            esc_email = _escape_typst(spec.email)
            contact_parts.append(f'#link("mailto:{spec.email}")[{esc_email}]')
        if spec.phone:
            contact_parts.append(_escape_typst(spec.phone))
        if spec.location:
            contact_parts.append(_escape_typst(spec.location))
        if spec.website:
            clean_url = spec.website.replace("https://", "").replace("http://", "").rstrip("/")
            contact_parts.append(f'#link("{spec.website}")[{_escape_typst(clean_url)}]')
        if spec.github:
            contact_parts.append(f'#link("{spec.github}")[GitHub]')
        if spec.linkedin:
            contact_parts.append(f'#link("{spec.linkedin}")[LinkedIn]')

        if contact_parts:
            sep = ' #text(fill: rgb("#94a3b8"))[·] '
            lines.append("  #v(2pt)")
            lines.append(f'  #text(size: 8.5pt, fill: rgb("#334155"))[{sep.join(contact_parts)}]')

        lines.append("]")
        lines.append("")

        # Summary
        if spec.summary:
            lines.append('#section_heading("Professional Summary")')
            lines.append(f"{_escape_typst(spec.summary)}\n")

        # Experience
        if spec.experience:
            lines.append('#section_heading("Experience")')
            for exp in spec.experience:
                date_range = f"{exp.start_date} – {exp.end_date or 'Present'}"
                loc_part = f" · {exp.location}" if exp.location else ""
                meta_right = f"{date_range}{loc_part}"

                lines.append("#grid(")
                lines.append("  columns: (1fr, auto),")
                lines.append("  align: (left + horizon, right + horizon),")
                lines.append("  [")
                lines.append(
                    f'    *{_escape_typst(exp.position)}* #text(fill: rgb("#94a3b8"))[|] '
                    f'#text(fill: rgb("#334155"))[*{_escape_typst(exp.company)}*]'
                )
                lines.append("  ],")
                lines.append("  [")
                lines.append(
                    f'    #text(size: 8.5pt, fill: rgb("#64748b"))[{_escape_typst(meta_right)}]'
                )
                lines.append("  ]")
                lines.append(")")

                for hl in exp.highlights:
                    lines.append(f"- {_escape_typst(hl)}")

                if exp.technologies:
                    techs = ", ".join(_escape_typst(t) for t in exp.technologies)
                    lines.append(
                        f'#text(size: 8.5pt, fill: rgb("#475569"))[*Technologies:* {techs}]\n'
                    )
                else:
                    lines.append("#v(2pt)")

        # Education
        if spec.education:
            lines.append('#section_heading("Education")')
            for edu in spec.education:
                degree_field = edu.degree
                if edu.field_of_study:
                    degree_field = f"{edu.degree} in {edu.field_of_study}"

                date_range = ""
                if edu.start_date and edu.end_date:
                    date_range = f"{edu.start_date} – {edu.end_date}"
                elif edu.end_date:
                    date_range = edu.end_date
                elif edu.start_date:
                    date_range = edu.start_date

                loc_part = f" · {edu.location}" if edu.location else ""
                meta_right = f"{date_range}{loc_part}"

                lines.append("#grid(")
                lines.append("  columns: (1fr, auto),")
                lines.append("  align: (left + horizon, right + horizon),")
                lines.append("  [")
                lines.append(
                    f'    *{_escape_typst(degree_field)}* #text(fill: rgb("#94a3b8"))[|] '
                    f'#text(fill: rgb("#334155"))[_{_escape_typst(edu.institution)}_]'
                )
                lines.append("  ],")
                lines.append("  [")
                if meta_right:
                    lines.append(
                        f'    #text(size: 8.5pt, fill: rgb("#64748b"))[{_escape_typst(meta_right)}]'
                    )
                lines.append("  ]")
                lines.append(")")

                for hl in edu.highlights:
                    lines.append(f"- {_escape_typst(hl)}")
                lines.append("#v(2pt)")

        # Skills
        if spec.skills:
            lines.append('#section_heading("Skills & Expertise")')
            for cat in spec.skills:
                skill_list = ", ".join(_escape_typst(s) for s in cat.skills)
                lines.append(f"- *{_escape_typst(cat.name)}:* {skill_list}")
            lines.append("")

        # Projects
        if spec.projects:
            lines.append('#section_heading("Projects")')
            for proj in spec.projects:
                proj_title = f"*{_escape_typst(proj.name)}*"
                if proj.url:
                    proj_title += f' #text(size: 8pt)[(#link("{proj.url}")[link])]'

                lines.append(f"{proj_title}")
                if proj.description:
                    lines.append(f"{_escape_typst(proj.description)}")

                for hl in proj.highlights:
                    lines.append(f"- {_escape_typst(hl)}")

                if proj.technologies:
                    techs = ", ".join(_escape_typst(t) for t in proj.technologies)
                    lines.append(
                        f'#text(size: 8.5pt, fill: rgb("#475569"))[*Technologies:* {techs}]\n'
                    )
                else:
                    lines.append("#v(2pt)")

        markup = "\n".join(lines)
        return cls.compile_markup(markup)

    @classmethod
    def generate_report(cls, spec: DocumentSpec) -> bytes:
        """Generate a publication-quality PDF report from structured DocumentSpec."""
        palette = THEME_PALETTES.get(spec.theme, THEME_PALETTES["slate"])
        primary = palette["primary"]
        accent = palette["accent"]
        bg_callout = palette["bg_callout"]
        border = palette["border"]
        muted = palette["muted"]

        # If any section contains base64 images, handle with tempdir
        has_images = any(s.image_base64 for s in spec.sections)

        lines: list[str] = []
        lines.append('#set page(paper: "a4", margin: (x: 2cm, top: 2.2cm, bottom: 2.2cm))')
        lines.append(
            '#set text(font: ("Liberation Sans", "DejaVu Sans", "Arial", "Helvetica"), '
            'size: 10pt, fill: rgb("#1e293b"))'
        )
        lines.append("#set par(justify: true, leading: 0.65em)")
        lines.append("")

        # Title banner
        lines.append("#align(center)[")
        lines.append(
            f'  #text(size: 20pt, weight: "bold", fill: rgb("{primary}"))[{_escape_typst(spec.title)}] \\'
        )
        if spec.subtitle:
            lines.append(
                f'  #v(2pt)\n  #text(size: 12pt, fill: rgb("{muted}"), weight: "medium")'
                f"[{_escape_typst(spec.subtitle)}] \\"
            )
        if spec.author:
            lines.append(
                f'  #v(2pt)\n  #text(size: 9pt, fill: rgb("{muted}"))'
                f"[Author: {_escape_typst(spec.author)}] \\"
            )
        lines.append("]")
        lines.append(f'#v(4pt)\n#line(length: 100%, stroke: 1.5pt + rgb("{accent}"))\n#v(10pt)\n')

        image_files: dict[str, bytes] = {}

        for s_idx, sec in enumerate(spec.sections):
            if sec.heading:
                eqs = "=" * sec.level
                lines.append(f"{eqs} {_escape_typst(sec.heading)}\n")

            for para in sec.paragraphs:
                lines.append(f"{_escape_typst(para)}\n")

            for bullet in sec.bullets:
                lines.append(f"- {_escape_typst(bullet)}")
            if sec.bullets:
                lines.append("")

            if sec.callout:
                lines.append("#v(4pt)")
                lines.append(
                    f'#rect(fill: rgb("{bg_callout}"), stroke: (left: 3.5pt + rgb("{accent}")), '
                    f"inset: 10pt, radius: (right: 4pt), width: 100%)["
                )
                lines.append(f"  {_escape_typst(sec.callout)}")
                lines.append("]")
                lines.append("#v(4pt)\n")

            if sec.table:
                cls._render_table(sec.table, border, lines)

            if sec.image_base64:
                img_filename = f"img_{s_idx}.png"
                try:
                    b64_str = sec.image_base64
                    if "," in b64_str:
                        b64_str = b64_str.split(",", 1)[1]
                    b64_padded = b64_str + "=" * (-len(b64_str) % 4)
                    img_bytes = base64.b64decode(b64_padded)
                    image_files[img_filename] = img_bytes
                    lines.append("#align(center)[")
                    lines.append(f'  #image("{img_filename}", width: 75%)')
                    if sec.image_caption:
                        lines.append(
                            f'  #v(2pt)\n  #text(size: 8.5pt, fill: rgb("{muted}"), style: "italic")'
                            f"[{_escape_typst(sec.image_caption)}]"
                        )
                    lines.append("]\n")
                except (ValueError, TypeError):
                    continue

        markup = "\n".join(lines)

        if has_images and image_files:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                for fname, fbytes in image_files.items():
                    (tmp_path / fname).write_bytes(fbytes)
                main_typ = tmp_path / "main.typ"
                main_typ.write_text(markup, encoding="utf-8")
                compiled: bytes = typst.compile(main_typ)
                return compiled

        return cls.compile_markup(markup)

    @classmethod
    def _render_table(cls, table: TableSpec, border_color: str, lines: list[str]) -> None:
        """Helper to format a TableSpec into Typst table syntax."""
        num_cols = len(table.headers)
        if num_cols == 0:
            return

        lines.append("#v(4pt)")
        lines.append("#table(")
        lines.append(f"  columns: {num_cols},")
        lines.append('  fill: (col, row) => if row == 0 { rgb("#f1f5f9") } else { none },')
        lines.append(
            f'  stroke: (x, y) => if y == 0 {{ (bottom: 1.2pt + rgb("{border_color}")) }} '
            f'else {{ 0.5pt + rgb("#e2e8f0") }},'
        )

        # Headers
        header_cells = [f"[*{_escape_typst(h)}*]" for h in table.headers]
        lines.append(f"  {', '.join(header_cells)},")

        # Rows
        for row in table.rows:
            row_cells = [f"[{_escape_typst(c)}]" for c in row]
            # Pad if row has fewer columns
            while len(row_cells) < num_cols:
                row_cells.append("[]")
            lines.append(f"  {', '.join(row_cells[:num_cols])},")

        lines.append(")")
        lines.append("#v(6pt)\n")

    @classmethod
    def generate(cls, spec: ResumeSpec | DocumentSpec | str) -> bytes:
        """Generic entry point dispatching to appropriate generator."""
        if isinstance(spec, ResumeSpec):
            return cls.generate_resume(spec)
        elif isinstance(spec, DocumentSpec):
            return cls.generate_report(spec)
        elif isinstance(spec, str):
            return cls.compile_markup(spec)
        raise ValueError(f"Unsupported spec type for TypstGenerator: {type(spec)}")
