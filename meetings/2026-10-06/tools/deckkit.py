# -*- coding: utf-8 -*-
"""Shared drawing helpers for the group-meeting deck (used by
build_group_meeting_pptx.py-era scripts and revise_meeting_pptx_v2.py).

Every helper records a PRIMS entry so scripts/render_preview.py can
reproduce the slide; pass record=False when re-drawing for a diff.
"""
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

# ---------------------------------------------------------------- design system
BASE = "F5F2EC"      # warm off-white page
INK = "1F2933"       # deep slate (cover + bands)
WHITE = "FFFFFF"
CARD_LINE = "E4DED4"
BODY = "4A5560"      # body gray
MUTED = "98A2AD"
CORAL = "C9553F"     # questions / 探头-side accent
TEAL = "2E7F86"      # data / host
AMBER = "D98A2B"     # 待确认 / 服务器
TEAL_LT = "8FC0C4"
TINT_ROW = "F7F4EF"
FONT_LAT = "Segoe UI"
FONT_EA = "Microsoft YaHei"

SW, SH = 13.333, 7.5
M = 0.9                      # side margin
CW = SW - 2 * M              # 11.533 content width
CONTENT_TOP = 1.85
BAND_TOP, BAND_H = 6.5, 0.62

PRIMS = []


def rec(p):
    PRIMS.append(p)


def set_font(run, size, bold, color):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.name = FONT_LAT
    f.color.rgb = RGBColor.from_string(color)
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", FONT_EA)


def fill_tf(tf, paras, anchor=None, margins=(0.03, 0.02, 0.03, 0.02)):
    tf.word_wrap = True
    tf.margin_left, tf.margin_top, tf.margin_right, tf.margin_bottom = (
        Inches(margins[0]), Inches(margins[1]), Inches(margins[2]), Inches(margins[3]))
    if anchor == "MIDDLE":
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    for i, spec in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER,
                       "r": PP_ALIGN.RIGHT}[spec.get("align", "l")]
        if spec.get("space_after") is not None:
            p.space_after = Pt(spec["space_after"])
        if spec.get("space_before"):
            p.space_before = Pt(spec["space_before"])
        if spec.get("line_spacing"):
            p.line_spacing = spec["line_spacing"]
        run = p.add_run()
        run.text = spec["text"]
        set_font(run, spec.get("size", 16), spec.get("bold", False),
                 spec.get("color", BODY))


def rect(slide, l, t, w, h, fill=None, rounded=False, radius=0.06,
         line_color=None, line_w=1.0, shape=MSO_SHAPE.RECTANGLE, text=None,
         align="l", anchor=None, pad=(0.03, 0.02, 0.03, 0.02), record=True):
    if rounded:
        shape = MSO_SHAPE.ROUNDED_RECTANGLE
    shp = slide.shapes.add_shape(shape, Inches(l), Inches(t), Inches(w), Inches(h))
    if rounded:
        try:
            shp.adjustments[0] = radius
        except Exception:
            pass
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = RGBColor.from_string(fill)
    else:
        shp.fill.background()
    if line_color:
        shp.line.color.rgb = RGBColor.from_string(line_color)
        shp.line.width = Pt(line_w)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    paras = []
    if text is not None:
        if isinstance(text, str):
            text = [{"text": text}]
        fill_tf(shp.text_frame, text, anchor=anchor, margins=pad)
        paras = text
    if record:
        rec({"t": "rect", "l": l, "t_": t, "w": w, "h": h, "fill": fill or "FFFFFF",
             "alpha": None, "rounded": rounded, "radius": radius})
        if paras:
            rec({"t": "text", "l": l, "t_": t, "w": w, "h": h, "paras": paras,
                 "anchor": anchor or "", "align": paras[0].get("align", "l")})
    return shp


def txt(slide, l, t, w, h, paras, anchor=None, align=None, record=True):
    if isinstance(paras, dict):
        paras = [paras]
    if align:
        for p in paras:
            p.setdefault("align", align)
    box = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    fill_tf(box.text_frame, paras, anchor=anchor)
    if record:
        rec({"t": "text", "l": l, "t_": t, "w": w, "h": h, "paras": paras,
             "anchor": anchor or "", "align": paras[0].get("align", "l")})
    return box


def img(slide, path, l, t, w, h, record=True):
    pic = slide.shapes.add_picture(path, Inches(l), Inches(t), Inches(w), Inches(h))
    if record:
        rec({"t": "img", "path": path, "l": l, "t_": t, "w": w, "h": h})
    return pic


def header(slide, kicker, title, accent):
    txt(slide, M, 0.42, CW, 0.32,
        [{"text": kicker, "size": 14, "bold": True, "color": accent}])
    txt(slide, M, 0.74, CW, 0.78,
        [{"text": title, "size": 32, "bold": True, "color": INK}])
    rect(slide, M, 1.585, CW, 0.018, fill=CARD_LINE)
    rect(slide, M, 1.57, 1.5, 0.05, fill=accent)


def band(slide, text, accent=CORAL):
    rect(slide, M, BAND_TOP, CW, BAND_H, fill=INK, rounded=True, radius=0.09)
    rect(slide, M + 0.3, BAND_TOP + BAND_H / 2 - 0.06, 0.12, 0.12, fill=accent)
    txt(slide, M + 0.56, BAND_TOP, CW - 0.86, BAND_H,
        [{"text": text, "size": 18, "bold": True, "color": WHITE}],
        anchor="MIDDLE")


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def tinted_row(slide, x, y, w, h, number, text, size=16):
    """Numbered tinted row used on the confirmation slide."""
    return rect(slide, x, y, w, h, fill=TINT_ROW, rounded=True, radius=0.12,
                text=[{"text": "%s   %s" % (number, text), "size": size,
                       "color": INK, "line_spacing": 1.22}],
                anchor="MIDDLE", pad=(0.24, 0.04, 0.16, 0.04))
