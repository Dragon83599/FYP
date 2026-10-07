# -*- coding: utf-8 -*-
"""Build the 2026-10-06 group-meeting deck (6 slides) from
docs/group-meeting-2026-10-06-content-outline.md.

Run:  python build_group_meeting_pptx.py
Emits: group-meeting-2026-10-06.pptx  +  %LOCALAPPDATA%\\Temp\\deck_prims.json
"""
import json
import os
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[3]
MEETING_DIR = ROOT / "meetings" / "2026-10-06"
OUT = MEETING_DIR / "group-meeting-2026-10-06.pptx"
TMP = Path(os.environ.get("LOCALAPPDATA", r"C:\Temp")) / "Temp"
PRIMS_PATH = TMP / "deck_prims.json"
PRODUCT_CROP = TMP / "fyp-product-crop.png"
TRACE = TMP / "heart-sound-trace.png"
ARCH = ROOT / "assets" / "fyp-system-architecture.png"

# ---------------------------------------------------------------- design system
BASE = "F5F2EC"      # warm off-white page
INK = "1F2933"       # deep slate (cover + bands)
WHITE = "FFFFFF"
CARD_LINE = "E4DED4"
BODY = "4A5560"      # body gray
MUTED = "98A2AD"
CORAL = "C9553F"     # heart / 我方-队友 accent, questions
TEAL = "2E7F86"      # data / host
AMBER = "D98A2B"     # 待确认 / 服务端
TEAL_LT = "8FC0C4"
FONT_LAT = "Segoe UI"
FONT_EA = "Microsoft YaHei"

SW, SH = 13.333, 7.5
M = 0.9                      # side margin
CW = SW - 2 * M              # 11.533 content width
CONTENT_TOP = 1.85
BAND_TOP, BAND_H = 6.5, 0.62

PRIMS = []
_anims = {}                  # slide_index -> list of click groups [shape_id, ...]


def rec(p):
    PRIMS.append(p)


def _set_font(run, size, bold, color):
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
        _set_font(run, spec.get("size", 16), spec.get("bold", False),
                  spec.get("color", BODY))


def rect(slide, l, t, w, h, fill=None, rounded=False, radius=0.06,
         line_color=None, line_w=1.0, shape=MSO_SHAPE.RECTANGLE, text=None,
         align="l", anchor=None, pad=(0.03, 0.02, 0.03, 0.02)):
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
    rec({"t": "rect", "l": l, "t_": t, "w": w, "h": h, "fill": fill or "FFFFFF",
         "alpha": None, "rounded": rounded, "radius": radius})
    if paras:
        rec({"t": "text", "l": l, "t_": t, "w": w, "h": h, "paras": paras,
             "anchor": anchor or "", "align": paras[0].get("align", "l")})
    return shp


def txt(slide, l, t, w, h, paras, anchor=None, align=None):
    if isinstance(paras, dict):
        paras = [paras]
    if align:
        for p in paras:
            p.setdefault("align", align)
    box = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    fill_tf(box.text_frame, paras, anchor=anchor)
    rec({"t": "text", "l": l, "t_": t, "w": w, "h": h, "paras": paras,
         "anchor": anchor or "", "align": paras[0].get("align", "l")})
    return box


def img(slide, path, l, t, w, h):
    pic = slide.shapes.add_picture(path, Inches(l), Inches(t), Inches(w), Inches(h))
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


def card(slide, l, t, w, h, accent, heading, items, heading_size=21,
         body_size=16.5, head_h=0.56):
    """White card with a coloured header bar + bullet items."""
    rect(slide, l, t, w, h, fill=WHITE, line_color=CARD_LINE, line_w=1.0,
         rounded=True, radius=0.05)
    rect(slide, l, t, w, head_h, fill=accent, rounded=True, radius=0.06)
    rect(slide, l, t + head_h - 0.14, w, 0.14, fill=accent)  # square off the bottom
    rect(slide, l, t, w, head_h,
         text=[{"text": heading, "size": heading_size, "bold": True,
                "color": WHITE, "align": "l"}],
         anchor="MIDDLE", pad=(0.28, 0.02, 0.1, 0.02))
    paras = []
    for i, it in enumerate(items):
        paras.append({"text": "•  " + it, "size": body_size, "color": BODY,
                      "line_spacing": 1.25, "space_after": 8})
    txt(slide, l + 0.26, t + head_h + 0.18, w - 0.5, h - head_h - 0.34, paras)
    return []  # shapes are registered by index later


# ================================================================ build
prs = Presentation()
prs.slide_width, prs.slide_height = Inches(SW), Inches(SH)
BLANK = prs.slide_layouts[6]


def new_slide(bg):
    s = prs.slides.add_slide(BLANK)
    rec({"t": "slide", "bg": bg})
    return s


# ---------------------------------------------------------------- slide 1 cover
s = new_slide(INK)
rect(s, 0, 0, 0.16, SH, fill=CORAL)
txt(s, M, 1.3, 6.5, 0.34,
    [{"text": "FINAL YEAR PROJECT · 组会汇报", "size": 14, "bold": True,
      "color": TEAL_LT}])
txt(s, M, 1.72, 6.6, 2.05,
    [{"text": "便携式心音采集与", "size": 40, "bold": True, "color": WHITE,
      "line_spacing": 1.18, "space_after": 0},
     {"text": "AI 分析系统", "size": 40, "bold": True, "color": WHITE,
      "line_spacing": 1.18}])
rect(s, M, 3.98, 1.4, 0.06, fill=CORAL)
txt(s, M, 4.26, 6.5, 0.5,
    [{"text": "本次汇报：近期进展与待确认事项", "size": 20, "color": "D8DEE4"}])
txt(s, M, 4.98, 6.5, 0.4,
    [{"text": "汇报人：庞舒瑞     2026-10-06     预计 5 分钟", "size": 16,
      "color": MUTED}])
rect(s, M, 5.45, 5.1, 0.5, fill=None, line_color="3A4552", line_w=1.25,
     rounded=True, radius=0.4,
     text=[{"text": "汇报路线：近期进展  →  待确认事项  →  下一步",
            "size": 15, "color": "D8DEE4", "align": "c"}],
     anchor="MIDDLE", pad=(0.1, 0.02, 0.1, 0.02))
img(s, TRACE, M, 6.06, 6.3, 1.092)   # 1500x260 native ratio
# product render card
rect(s, 7.6, 1.2, 4.9, 4.75, fill=WHITE, rounded=True, radius=0.05)
img(s, PRODUCT_CROP, 7.8, 1.4, 4.5, 3.913)   # 1150x1000
txt(s, 7.8, 5.42, 4.5, 0.4,
    [{"text": "产品外观与交互示意（渲染图）", "size": 13, "color": MUTED,
      "align": "c"}])
notes(s, "各位老师、同学好。这次汇报控制在 5 分钟左右，主要讲两件事："
         "这几天我们做了什么，以及还需要老师确认什么。")

# ---------------------------------------------------------------- slide 2
s = new_slide(BASE)
header(s, "进展 · 01", "这几天做了什么", TEAL)
cw2 = (CW - 0.35) / 2
ch2 = 2.0
cards2 = [
    ("1", "梳理系统整体方案",
     "心音采集 → 组帧 → 无线传输 → 主机分析 → 结果展示，链路已理清"),
    ("2", "整理设备端数据接口初稿",
     "8 kHz、16-bit、单声道，20 ms 一帧；加入序号、时间戳与 CRC 校验"),
    ("3", "制作架构图与产品示意图",
     "完成系统架构图与产品外观、交互示意图"),
    ("4", "启动软硬件接口梳理",
     "提前统一软硬件之间的接口，避免后续返工"),
]
ids2 = []
for i, (num, head, body) in enumerate(cards2):
    cx = M + (i % 2) * (cw2 + 0.35)
    cy = CONTENT_TOP + (i // 2) * (ch2 + 0.35)
    accent = TEAL if i % 2 == 0 else CORAL
    grp = []
    grp.append(rect(s, cx, cy, cw2, ch2, fill=WHITE, line_color=CARD_LINE,
                    rounded=True, radius=0.05).shape_id)
    grp.append(rect(s, cx + 0.3, cy + 0.28, 0.72, 0.72, fill=accent,
                    rounded=True, radius=0.22,
                    text=[{"text": num, "size": 28, "bold": True,
                           "color": WHITE, "align": "c"}],
                    anchor="MIDDLE", pad=(0, 0, 0, 0)).shape_id)
    grp.append(txt(s, cx + 1.2, cy + 0.26, cw2 - 1.5, 0.5,
                   [{"text": head, "size": 21, "bold": True, "color": INK}]).shape_id)
    grp.append(txt(s, cx + 1.2, cy + 0.78, cw2 - 1.5, ch2 - 0.9,
                   [{"text": body, "size": 16.5, "color": BODY,
                     "line_spacing": 1.3}]).shape_id)
    ids2.append(grp)
_anims[1] = ids2
band(s, "方案与接口初稿已成型，下一步进入软硬件接口冻结", TEAL)
notes(s, "这几天我们主要做了三件事。第一，梳理了系统整体方案，把心音采集、"
         "数据传输、主机分析和结果显示这条链路理清了。第二，整理了设备端的数据接口初稿，"
         "确定 8 kHz 采集、20 ms 一帧，并加入序号、时间戳和 CRC 校验，"
         "方便后面检查丢帧和数据损坏。第三，制作了系统架构图和产品示意图，"
         "同时开始整理硬件与软件之间的接口。")

# ---------------------------------------------------------------- slide 3
s = new_slide(BASE)
header(s, "进展 · 02", "目前方案", CORAL)
rows3 = [
    ("设备端", TEAL, "心音传感器 · 模拟前端 · MCU 采集与组帧"),
    ("传输端", TEAL, "数据无线发送到主机，同时保留本地存储"),
    ("主机端", TEAL, "心音数据处理，本地 AI 推理与波形展示"),
    ("服务端", AMBER, "是否上传、上传什么，尚未确定"),
]
ids3 = []
for i, (tag, accent, body) in enumerate(rows3):
    ry = 1.98 + i * 1.11
    grp = []
    grp.append(rect(s, M, ry, 1.15, 0.38, fill=accent, rounded=True, radius=0.3,
                    text=[{"text": tag, "size": 15, "bold": True,
                           "color": WHITE, "align": "c"}],
                    anchor="MIDDLE", pad=(0, 0, 0, 0)).shape_id)
    grp.append(txt(s, M, ry + 0.46, 4.3, 0.6,
                   [{"text": body, "size": 16, "color": BODY,
                     "line_spacing": 1.3}]).shape_id)
    ids3.append(grp)
grp_card = rect(s, 5.4, 1.86, 7.03, 4.39, fill=WHITE, line_color=CARD_LINE,
                rounded=True, radius=0.04).shape_id
grp_img = img(s, ARCH, 5.52, 1.98, 6.79, 4.15).shape_id
ids3.insert(0, [grp_card, grp_img])
_anims[2] = ids3
band(s, "四段链路已明确，服务端是否上传是唯一待定项", AMBER)
notes(s, "目前方案分四段：设备端完成心音传感、模拟前端与 MCU 采集组帧；"
         "传输端把数据无线发到主机，同时保留本地存储；主机端做数据处理、"
         "本地 AI 推理和波形展示；服务端目前还没有确定是否上传，"
         "以及上传原始数据、特征还是分析结果。协议参数这里不展开，"
         "老师如果追问，我们按详细参考文档补充说明。")

# ---------------------------------------------------------------- slide 4
s = new_slide(BASE)
header(s, "进展 · 03", "团队分工", AMBER)
cols4 = [
    ("我负责", TEAL, [
        "AI 数据集整理、模型训练、评估与优化",
        "模型量化、边缘部署与 MCU 端推理",
        "便携显示、心音波形与 GUI",
        "无线通信、数据协议与上位机传输",
        "系统软件集成、延迟与功耗测试",
    ]),
    ("队友负责", CORAL, [
        "麦克风选型、心音采集与模拟前端",
        "放大、滤波、ADC 采样与心音预处理",
        "扬声器回放电路",
        "主控板、电源、电池与充电模块",
        "PCB 设计、打样、焊接、外壳与硬件调试",
    ]),
    ("共同负责", AMBER, [
        "冻结主控、采样率、数据格式与通信协议",
        "确认模型输入输出接口",
        "用真实设备采集并验证数据",
        "完成系统联调、测试与最终演示",
    ]),
]
cw4 = (CW - 2 * 0.3) / 3
ids4 = []
for i, (head, accent, items) in enumerate(cols4):
    cx = M + i * (cw4 + 0.3)
    cy, ch4 = CONTENT_TOP, 4.35
    grp = []
    grp.append(rect(s, cx, cy, cw4, ch4, fill=WHITE, line_color=CARD_LINE,
                    rounded=True, radius=0.05).shape_id)
    grp.append(rect(s, cx, cy, cw4, 0.56, fill=accent, rounded=True, radius=0.07).shape_id)
    grp.append(rect(s, cx, cy + 0.42, cw4, 0.14, fill=accent).shape_id)
    grp.append(rect(s, cx, cy, cw4, 0.56,
                    text=[{"text": head, "size": 20, "bold": True,
                           "color": WHITE, "align": "c"}],
                    anchor="MIDDLE", pad=(0, 0, 0, 0)).shape_id)
    gap4 = 8 if len(items) > 4 else 17
    paras = [{"text": "•  " + it, "size": 16, "color": BODY,
              "line_spacing": 1.25, "space_after": gap4} for it in items]
    grp.append(txt(s, cx + 0.26, cy + 0.78, cw4 - 0.52, ch4 - 0.95, paras).shape_id)
    ids4.append(grp)
_anims[3] = ids4
band(s, "接口冻结与系统联调是双方共同的交付节点", AMBER)
notes(s, "分工方面，我主要负责 AI 模型训练与边缘部署、显示、无线通信和软件集成；"
         "队友负责心音采集、模拟前端、回放、电源、PCB 和硬件结构。"
         "我们会共同冻结接口，并完成联调。")

# ---------------------------------------------------------------- slide 5
s = new_slide(BASE)
header(s, "待确认 · 01", "需要老师确认", CORAL)
cw5 = (CW - 0.4) / 2
ch5 = 4.35
q5 = [
    ("1 · 最终成品形态", CORAL, [
        "一体式：探头、主机与屏幕放在同一外壳内",
        "二体式：探头与主机分离",
        "二体式时，探头与主机之间：有线还是无线？",
    ], "影响：外壳结构与探头—主机的连接方式"),
    ("2 · 是否需要上传服务器", AMBER, [
        "是否需要服务器、账号、历史记录与远程查看",
        "上传内容：原始音频、特征，还是仅最终结果",
        "上传时机：实时上传，还是结束后统一上传",
    ], "影响：是否需要服务端，以及上传的数据内容"),
]
ids5 = []
for i, (head, accent, items, hint) in enumerate(q5):
    cx = M + i * (cw5 + 0.4)
    grp = []
    grp.append(rect(s, cx, CONTENT_TOP, cw5, ch5, fill=WHITE,
                    line_color=CARD_LINE, rounded=True, radius=0.05).shape_id)
    grp.append(rect(s, cx, CONTENT_TOP, cw5, 0.6, fill=accent,
                    rounded=True, radius=0.07).shape_id)
    grp.append(rect(s, cx, CONTENT_TOP + 0.46, cw5, 0.14, fill=accent).shape_id)
    grp.append(rect(s, cx, CONTENT_TOP, cw5, 0.6,
                    text=[{"text": head, "size": 21, "bold": True,
                           "color": WHITE, "align": "l"}],
                    anchor="MIDDLE", pad=(0.3, 0.02, 0.1, 0.02)).shape_id)
    row_h, row_gap = 0.8, 0.08
    for n, it in enumerate(items):
        ry = CONTENT_TOP + 0.81 + n * (row_h + row_gap)
        tint = "F7F4EF"
        grp.append(rect(s, cx + 0.3, ry, cw5 - 0.6, row_h, fill=tint,
                        rounded=True, radius=0.12,
                        text=[{"text": "%d.   %s" % (n + 1, it), "size": 16.5,
                               "color": INK, "line_spacing": 1.22}],
                        anchor="MIDDLE",
                        pad=(0.24, 0.04, 0.16, 0.04)).shape_id)
    tint = "F4E6E0" if accent == CORAL else "F8EEDF"
    grp.append(rect(s, cx + 0.3, CONTENT_TOP + ch5 - 0.85, cw5 - 0.6, 0.62,
                    fill=tint, rounded=True, radius=0.12,
                    text=[{"text": hint, "size": 16, "bold": True,
                           "color": (CORAL if accent == CORAL else "A9691F"),
                           "align": "l"}],
                    anchor="MIDDLE", pad=(0.24, 0.02, 0.12, 0.02)).shape_id)
    ids5.append(grp)
_anims[4] = ids5
band(s, "AI 推理固定在本地主机运行，服务器不承担模型推理", CORAL)
notes(s, "目前主要有两个问题想请老师确认：最终成品是一体式还是二体式"
         "（如果是二体式，探头和主机之间走有线还是无线）；"
         "数据是否需要上传服务器，如果需要，上传什么内容、什么时候传。"
         "另外我们目前计划把 AI 推理固定放在本地主机，不放到服务器上。")

# ---------------------------------------------------------------- slide 6
s = new_slide(BASE)
header(s, "计划 · 01", "下一步", TEAL)
steps = [
    ("1", "冻结方案形态", "根据老师意见确定一体式或二体式", CORAL),
    ("2", "明确上传策略", "有需求再定上传内容与上传时机", AMBER),
    ("3", "确认硬件接口", "与硬件同学确认传感器、主控与 ADC 接口", TEAL),
    ("4", "协议联调与测试", "完成协议联调、持续采集与主机端数据处理", CORAL),
]
cw6 = (CW - 3 * 0.3) / 4
ch6 = 3.2
ids6 = []
for i, (num, head, body, accent) in enumerate(steps):
    cx = M + i * (cw6 + 0.3)
    cy = 2.2
    grp = []
    grp.append(rect(s, cx, cy, cw6, ch6, fill=WHITE, line_color=CARD_LINE,
                    rounded=True, radius=0.06).shape_id)
    grp.append(rect(s, cx, cy, cw6, 0.09, fill=accent, rounded=True, radius=0.4).shape_id)
    grp.append(rect(s, cx + 0.28, cy + 0.34, 0.6, 0.6, fill=accent,
                    rounded=True, radius=0.5,
                    text=[{"text": num, "size": 22, "bold": True,
                           "color": WHITE, "align": "c"}],
                    anchor="MIDDLE", pad=(0, 0, 0, 0)).shape_id)
    grp.append(txt(s, cx + 0.28, cy + 1.1, cw6 - 0.56, 0.78,
                   [{"text": head, "size": 19, "bold": True, "color": INK,
                     "line_spacing": 1.2}]).shape_id)
    grp.append(txt(s, cx + 0.28, cy + 1.96, cw6 - 0.56, 1.12,
                   [{"text": body, "size": 15.5, "color": BODY,
                     "line_spacing": 1.3}]).shape_id)
    if i < 3:
        ax = cx + cw6 + 0.03
        grp.append(txt(s, ax, cy + 1.2, 0.24, 0.5,
                       [{"text": "›", "size": 26, "bold": True, "color": MUTED,
                         "align": "c"}]).shape_id)
    ids6.append(grp)
_anims[5] = ids6
band(s, "老师确认后即可开始协议联调与采集测试", TEAL)
notes(s, "确认之后，我们就可以继续冻结硬件连接方案，并开始协议联调和采集测试："
         "先根据老师意见冻结一体式或二体式方案，有上传需求就确定上传内容与时机，"
         "再和硬件同学确认传感器、主控和 ADC 接口，最后完成协议联调、"
         "持续采集测试和主机端数据处理流程。")

# ================================================================ save + prims
prs.save(OUT)
json.dump(PRIMS, open(PRIMS_PATH, "w", encoding="utf-8"), ensure_ascii=False)
json.dump({str(k): v for k, v in _anims.items()},
          open(os.path.join(TMP, "deck_anim_groups.json"), "w"))
print("saved:", OUT)
print("prims:", PRIMS_PATH, "slides:", len(prs.slides))
