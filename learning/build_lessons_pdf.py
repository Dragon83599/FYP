"""Render learning/lessons_L18-L36.md into a print/phone friendly PDF."""

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "learning" / "lessons_L18-L36.md"
OUTPUT = REPO / "learning" / "lessons_L18-L36.pdf"

FONTS = Path(r"C:\Windows\Fonts")


def register_fonts():
    """Register a CJK sans font, a bold variant, and a mono-ish font for code."""
    available = {}
    try:
        pdfmetrics.registerFont(TTFont("CJK", str(FONTS / "msyh.ttc"), subfontIndex=0))
        available["body"] = "CJK"
    except Exception:
        pdfmetrics.registerFont(TTFont("CJK", str(FONTS / "msyh.ttf")))
        available["body"] = "CJK"

    try:
        pdfmetrics.registerFont(TTFont("CJKBD", str(FONTS / "msyhbd.ttc"), subfontIndex=0))
        available["bold"] = "CJKBD"
    except Exception:
        available["bold"] = available["body"]

    # NSimSun (index 1 of simsun.ttc) is monospaced, which keeps ASCII art aligned.
    try:
        pdfmetrics.registerFont(TTFont("CJKMONO", str(FONTS / "simsun.ttc"), subfontIndex=1))
        available["mono"] = "CJKMONO"
    except Exception:
        available["mono"] = available["body"]

    for family in (("CJKFam", "CJK", "CJKBD", "CJKBD"),):
        try:
            pdfmetrics.registerFontFamily(
                family[0], normal=family[1], bold=family[2], italic=family[1], boldItalic=family[3]
            )
        except Exception:
            pass
    return available


F = register_fonts()

styles = getSampleStyleSheet()


def st(name, **kw):
    base = dict(
        fontName=F["body"],
        fontSize=9.6,
        leading=15.2,
        textColor=colors.HexColor("#1b2430"),
        alignment=TA_LEFT,
        spaceBefore=0,
        spaceAfter=0,
    )
    base.update(kw)
    return ParagraphStyle(name, **base)


S_TITLE = st("t", fontName=F["bold"], fontSize=25, leading=32, textColor=colors.HexColor("#0f3d5c"))
S_SUB = st("s", fontSize=11, leading=17, textColor=colors.HexColor("#5b6b7c"))
S_H1 = st("h1", fontName=F["bold"], fontSize=17.5, leading=24, textColor=colors.HexColor("#0f3d5c"),
          spaceBefore=14, spaceAfter=7)
S_H2 = st("h2", fontName=F["bold"], fontSize=13.6, leading=20, textColor=colors.HexColor("#14708f"),
          spaceBefore=12, spaceAfter=5)
S_H3 = st("h3", fontName=F["bold"], fontSize=11.2, leading=17, textColor=colors.HexColor("#2d4a5c"),
          spaceBefore=9, spaceAfter=4)
S_BODY = st("b", spaceAfter=5)
S_LIST = st("l", leftIndent=13, bulletIndent=3, spaceAfter=2.5)
S_QUOTE = st("q", leftIndent=10, rightIndent=6, fontSize=9.3, leading=14.6,
             textColor=colors.HexColor("#3f5a6b"))
S_CODE = st("c", fontName=F["mono"], fontSize=8.2, leading=11.6,
            textColor=colors.HexColor("#12232e"))
S_CELL = st("cell", fontSize=8.6, leading=12.2)
S_CELLH = st("cellh", fontName=F["bold"], fontSize=8.6, leading=12.2,
             textColor=colors.HexColor("#0f3d5c"))


def esc(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r'<font name="%s" size="8.8">\1</font>' % F["mono"], text)
    return text


def esc_raw(text: str) -> str:
    """Escape only XML special characters, no markdown interpretation."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def code_markup(block_lines) -> str:
    """Preserve indentation and wrapping: leading spaces become non-breaking."""
    out = []
    for ln in block_lines:
        if not ln.strip():
            out.append("&nbsp;")
            continue
        lead_match = re.match(r"^[ \t]*", ln)
        lead = lead_match.group(0)
        body = esc_raw(ln[len(lead):])
        # Keep runs of 2+ spaces from being collapsed, but allow wrapping at single spaces.
        body = re.sub(r" {2,}", lambda m: "&nbsp;" * len(m.group(0)), body)
        out.append("&nbsp;" * len(lead) + body)
    return "<br/>".join(out)


def is_table_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s:\-|]+\|", line.strip()))


def split_row(line: str):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse(md: str):
    flow = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            box = Paragraph(code_markup(block), S_CODE)
            tbl = Table([[box]], colWidths=[168 * mm])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f2f6f9")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfdde6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            flow += [Spacer(1, 3), tbl, Spacer(1, 6)]
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and is_table_sep(lines[i + 1]):
            header = split_row(lines[i])
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            data = [[Paragraph(esc(c), S_CELLH) for c in header]]
            for r in rows:
                data.append([Paragraph(esc(c), S_CELL) for c in r])
            ncol = len(header)
            usable = 168 * mm
            widths = [usable / ncol] * ncol
            tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e4eef4")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c3d4de")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ]))
            flow += [Spacer(1, 3), tbl, Spacer(1, 7)]
            continue

        if stripped in ("---", "***", "___"):
            flow += [Spacer(1, 5), HRFlowable(width="100%", thickness=0.6,
                                              color=colors.HexColor("#c3d4de")), Spacer(1, 7)]
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        if stripped.startswith("### "):
            flow.append(Paragraph(esc(stripped[4:]), S_H3))
        elif stripped.startswith("## "):
            flow.append(Paragraph(esc(stripped[3:]), S_H2))
        elif stripped.startswith("# "):
            flow.append(Paragraph(esc(stripped[2:]), S_H1))
        elif stripped == ">":
            flow.append(Spacer(1, 4))
        elif stripped.startswith("> - "):
            flow.append(Paragraph(esc(stripped[4:]), S_QUOTE, bulletText="\u2022"))
        elif stripped.startswith("> "):
            flow.append(Paragraph(esc(stripped[2:]), S_QUOTE))
        elif re.match(r"^\d+\.\s", stripped):
            text = re.sub(r"^\d+\.\s", "", stripped)
            flow.append(Paragraph(esc(text), S_LIST, bulletText=re.match(r"^\d+", stripped).group(0) + "."))
        elif stripped.startswith("- "):
            flow.append(Paragraph(esc(stripped[2:]), S_LIST, bulletText="\u2022"))
        else:
            flow.append(Paragraph(esc(stripped), S_BODY))
        i += 1
    return flow


def decorate(canvas, doc):
    canvas.saveState()
    canvas.setFont(F["body"], 7.6)
    canvas.setFillColor(colors.HexColor("#8a99a6"))
    canvas.drawString(21 * mm, 12 * mm, "心音边缘 AI 学习课 L18-L36")
    canvas.drawRightString(189 * mm, 12 * mm, "第 %d 页" % doc.page)
    canvas.setStrokeColor(colors.HexColor("#dbe6ec"))
    canvas.setLineWidth(0.5)
    canvas.line(21 * mm, 15.5 * mm, 189 * mm, 15.5 * mm)
    canvas.restoreState()


def main():
    md = SOURCE.read_text(encoding="utf-8")
    story = parse(md)
    doc = BaseDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=21 * mm,
        rightMargin=21 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title="心音边缘 AI 学习课 L18-L36",
        author="FYP 学习笔记",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=decorate)])
    doc.build(story)
    print("wrote", OUTPUT, OUTPUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
