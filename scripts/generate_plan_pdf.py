from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "docs" / "期末計畫書撰寫摘要.md"
OUTPUT = Path(r"C:\tmp") / "AIDC_final_project_plan_v3.pdf"

KAIU = Path(r"C:\Windows\Fonts\kaiu.ttf")
TIMES = Path(r"C:\Windows\Fonts\times.ttf")
TIMES_BOLD = Path(r"C:\Windows\Fonts\timesbd.ttf")


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("KaiU", str(KAIU)))
    pdfmetrics.registerFont(TTFont("TimesNewRoman", str(TIMES)))
    pdfmetrics.registerFont(TTFont("TimesNewRoman-Bold", str(TIMES_BOLD)))


ASCII_RUN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_\-./:()&+, ]*[A-Za-z0-9)]|[A-Za-z0-9])")
INLINE_CODE = re.compile(r"`([^`]+)`")
STRONG = re.compile(r"\*\*([^*]+)\*\*")


def mixed_markup(text: str, *, bold_ascii: bool = False) -> str:
    """Escape Markdown-ish text and use Times New Roman for English/ASCII runs."""
    text = INLINE_CODE.sub(lambda match: match.group(1), text)
    text = STRONG.sub(r"\1", text)
    escaped = html.escape(text)

    def ascii_font(match: re.Match[str]) -> str:
        run = match.group(1)
        font = "TimesNewRoman-Bold" if bold_ascii else "TimesNewRoman"
        return f'<font name="{font}">{run}</font>'

    escaped = ASCII_RUN.sub(ascii_font, escaped)
    return escaped


def is_table_start(lines: list[str], idx: int) -> bool:
    if idx + 1 >= len(lines):
        return False
    return lines[idx].strip().startswith("|") and re.match(r"^\s*\|?\s*:?-{3,}", lines[idx + 1])


def split_table_row(line: str) -> list[str]:
    line = line.strip().strip("|")
    return [cell.strip() for cell in line.split("|")]


def build_table(rows: list[list[str]], styles: dict[str, ParagraphStyle]) -> Table:
    data = []
    for r_idx, row in enumerate(rows):
        style = styles["table_header"] if r_idx == 0 else styles["table_cell"]
        data.append([Paragraph(mixed_markup(cell, bold_ascii=(r_idx == 0)), style) for cell in row])

    col_count = max(len(row) for row in rows)
    available = A4[0] - 4 * cm
    col_widths = [available / col_count] * col_count
    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "KaiU"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEADING", (0, 0), (-1, -1), 11),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F0F0")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#888888")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("TimesNewRoman", 10)
    canvas.drawCentredString(A4[0] / 2, 1.1 * cm, str(doc.page))
    canvas.restoreState()


def make_styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "Base",
        fontName="KaiU",
        fontSize=12,
        leading=14.5,
        alignment=TA_LEFT,
        spaceBefore=0,
        spaceAfter=0,
        wordWrap="CJK",
        firstLineIndent=0,
    )
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base,
            fontSize=16,
            leading=20,
            alignment=TA_CENTER,
            spaceAfter=10,
        ),
        "h1": ParagraphStyle("H1", parent=base, fontSize=14, leading=18, spaceBefore=8, spaceAfter=4),
        "h2": ParagraphStyle("H2", parent=base, fontSize=12, leading=15, spaceBefore=6, spaceAfter=3),
        "body": base,
        "list": ParagraphStyle("List", parent=base, leftIndent=16, firstLineIndent=-16),
        "table_cell": ParagraphStyle("TableCell", parent=base, fontSize=9, leading=11, wordWrap="CJK"),
        "table_header": ParagraphStyle("TableHeader", parent=base, fontSize=9, leading=11, wordWrap="CJK"),
    }


def build_story(markdown: str) -> list:
    styles = make_styles()
    lines = markdown.splitlines()
    story: list = []
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_buffer:
            return
        text = " ".join(item.strip() for item in paragraph_buffer if item.strip())
        paragraph_buffer.clear()
        if text:
            story.append(Paragraph(mixed_markup(text), styles["body"]))

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            idx += 1
            continue

        if is_table_start(lines, idx):
            flush_paragraph()
            rows = [split_table_row(lines[idx])]
            idx += 2
            while idx < len(lines) and lines[idx].strip().startswith("|"):
                rows.append(split_table_row(lines[idx]))
                idx += 1
            story.append(build_table(rows, styles))
            story.append(Spacer(1, 4))
            continue

        if stripped.startswith("# "):
            flush_paragraph()
            title = stripped[2:].strip()
            story.append(Paragraph(mixed_markup(title, bold_ascii=True), styles["title"]))
        elif stripped.startswith("## "):
            flush_paragraph()
            heading = stripped[3:].strip()
            if heading.startswith("(七)"):
                story.append(PageBreak())
            story.append(Paragraph(mixed_markup(heading, bold_ascii=True), styles["h1"]))
        elif stripped.startswith("### "):
            flush_paragraph()
            heading = stripped[4:].strip()
            story.append(Paragraph(mixed_markup(heading, bold_ascii=True), styles["h2"]))
        elif re.match(r"^\d+\.\s+", stripped):
            flush_paragraph()
            story.append(Paragraph(mixed_markup(stripped), styles["list"]))
        elif stripped.startswith("- "):
            flush_paragraph()
            story.append(Paragraph(mixed_markup("• " + stripped[2:]), styles["list"]))
        elif stripped == "---":
            flush_paragraph()
            story.append(PageBreak())
        else:
            paragraph_buffer.append(stripped)
        idx += 1

    flush_paragraph()
    return story


def main() -> None:
    register_fonts()
    markdown = INPUT.read_text(encoding="utf-8")
    story = build_story(markdown)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=2.54 * cm,
        leftMargin=2.54 * cm,
        topMargin=2.54 * cm,
        bottomMargin=2.54 * cm,
        title="期末計畫書撰寫摘要",
        author="Alarm RAG",
    )
    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    print(OUTPUT)


if __name__ == "__main__":
    main()
