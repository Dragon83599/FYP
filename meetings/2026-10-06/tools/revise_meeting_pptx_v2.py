# -*- coding: utf-8 -*-
"""Revise group-meeting-2026-10-06.pptx to content-outline v2, IN PLACE.

The user hand-edited the deck after the first build (cover name -> "Pang
Shurui", slide 5 rewritten), so the build script is history: this script
edits the delivered file directly, by content-addressed markers, never by
blind index.

v2 target order (6 pages):
  1 cover | 2 这几天做了什么 | 3 系统总体架构 | 4 三部分职责
  5 团队分工 (untouched except kicker) | 6 需要老师确认 (+ 下一步 band)

Also rewrites %LOCALAPPDATA%\\Temp\\deck_anim_groups.json with the FINAL
shape ids so add_meeting_animations.py can rebuild timing safely.
"""
import json
import os
import sys
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from pptx.util import Inches

import deckkit as dk
from deckkit import band, header, img, notes, rect, txt

ROOT = Path(__file__).resolve().parents[3]
MEETING_DIR = ROOT / "meetings" / "2026-10-06"
DECK = MEETING_DIR / "group-meeting-2026-10-06.pptx"
TMP = Path(os.environ.get("LOCALAPPDATA", r"C:\Temp")) / "Temp"
GROUPS_OUT = TMP / "deck_anim_groups.json"
PRODUCT_CROP = TMP / "fyp-product-crop.png"
ARCH = ROOT / "assets" / "fyp-system-architecture.png"

PNS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


# ---------------------------------------------------------------- helpers
def find_slide(prs, marker):
    for s in prs.slides:
        for sh in s.shapes:
            if sh.has_text_frame and marker in sh.text_frame.text:
                return s
    raise SystemExit(f"marker not found: {marker}")


def wipe(slide):
    """Remove every shape + any p:timing (old spids would dangle)."""
    for el in list(slide._element):
        if el.tag == PNS + "timing":
            slide._element.remove(el)
    spTree = slide.shapes._spTree
    for sh in list(slide.shapes):
        spTree.remove(sh._element)


def set_text(slide, old, new):
    for sh in slide.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip() == old:
            para = sh.text_frame.paragraphs[0]
            if para.runs:
                para.runs[0].text = new
                for r in para.runs[1:]:
                    r.text = ""
                return True
    return False


def col_card(slide, x, y, w, h, accent, heading, items, body_size=16,
             head_h=0.5, gap=6, line_spacing=1.2):
    rect(slide, x, y, w, h, fill=dk.WHITE, line_color=dk.CARD_LINE,
         rounded=True, radius=0.05)
    rect(slide, x, y, w, head_h, fill=accent, rounded=True, radius=0.07)
    rect(slide, x, y + head_h - 0.13, w, 0.13, fill=accent)
    rect(slide, x, y, w, head_h,
         text=[{"text": heading, "size": 20, "bold": True, "color": dk.WHITE,
                "align": "c"}],
         anchor="MIDDLE", pad=(0.02, 0.02, 0.02, 0.02))
    paras = [{"text": "•  " + it, "size": body_size, "color": dk.BODY,
              "line_spacing": line_spacing, "space_after": gap} for it in items]
    txt(slide, x + 0.25, y + head_h + 0.16, w - 0.5, h - head_h - 0.3, paras)


def confirm_card(slide, cx, w, accent, heading, rows):
    y, h = dk.CONTENT_TOP, 4.35
    rect(slide, cx, y, w, h, fill=dk.WHITE, line_color=dk.CARD_LINE,
         rounded=True, radius=0.05)
    rect(slide, cx, y, w, 0.6, fill=accent, rounded=True, radius=0.07)
    rect(slide, cx, y + 0.46, w, 0.14, fill=accent)
    rect(slide, cx, y, w, 0.6,
         text=[{"text": heading, "size": 21, "bold": True, "color": dk.WHITE,
                "align": "l"}],
         anchor="MIDDLE", pad=(0.3, 0.02, 0.1, 0.02))
    for n, body in enumerate(rows):
        tinted(slide, cx + 0.3, 2.71 + n * 0.86, w - 0.6, 0.74, n + 1, body)


def tinted(slide, x, y, w, h, n, body, size=16):
    rect(slide, x, y, w, h, fill=dk.TINT_ROW, rounded=True, radius=0.12,
         text=[{"text": "%s.   %s" % (n, body), "size": size, "color": dk.INK,
                "line_spacing": 1.22}],
         anchor="MIDDLE", pad=(0.24, 0.04, 0.16, 0.04))


# ---------------------------------------------------------------- content
FLOW = [
    dict(x=0.9, w=3.05, fill=dk.TEAL, line=None, fg=dk.WHITE,
         text="探头（贴胸口）", size=18),
    dict(x=5.1, w=3.55, fill=dk.INK, line=None, fg=dk.WHITE,
         text="主机（AI + 屏幕 + 扬声器 + WiFi）", size=16),
    dict(x=9.8, w=2.63, fill=dk.WHITE, line=dk.AMBER, fg=dk.AMBER,
         text="服务器（数据存储）", size=18),
]
GAPS = [(3.95, 1.15, "BLE /  有线"), (8.65, 1.15, "WiFi / 手机网关")]

STATEMENTS = [
    "探头 + 主机构成便携式设备，服务器是外部平台",
    "主机在没有服务器时，也能完成接收、显示、播放和初步诊断",
    "服务器只负责数据存储，不参与实时诊断、模型训练与管理",
]

DUTY = [
    ("探头", dk.TEAL, [
        "只做可靠采集与预处理，不做 AI、不显示、不播放",
        "模拟前端：放大、20 Hz 高通、1 kHz 低通、抗混叠、偏置",
        "低功耗 MCU 连续采集，输出 4 kHz 或 8 kHz 心音数据",
        "默认 4 kHz PCM：20 ms = 80 samples、160 B，64 kbps",
    ]),
    ("主机", dk.INK, [
        "通过 BLE 或有线接口接收探头数据",
        "分片重组、CRC 校验、丢帧检测与 2–5 秒窗口缓存",
        "本地运行 int8 量化模型，输出类别、置信度和模型版本",
        "驱动屏幕显示波形、BPM、AI 结果和信号质量",
        "扬声器回放心音；负责 WiFi 上传、断网缓存与恢复补传",
        "原样保留探头的序号与时间戳",
    ]),
    ("服务器", dk.AMBER, [
        "只存储上传的心音数据、元数据与 AI 结果",
        "数据库保存索引，对象存储保存音频、波形等大文件",
        "提供最基础的上传、数据索引与读取/导出接口",
        "主机优先 WiFi 连接；手机网关可选配，非必要条件",
        "探头与主机优先评估 BLE，按结构确认结果定无线/有线",
    ]),
]
DUTY_X = [(0.9, 3.35), (4.55, 4.25), (9.10, 3.33)]

CONFIRM = [
    ("1 · 设备结构", dk.CORAL, [
        "已确定二体式：探头贴胸采集，主机独立显示与联网",
        "请老师确认：这个总体结构是否合适",
        "探头与主机：BLE 无线还是有线？蓝牙是硬性要求还是其他也行？",
        "请老师确认：主机形态是手持还是随身佩戴",
    ]),
    ("2 · 上传服务器的范围", dk.AMBER, [
        "只上传 AI 结果与异常告警，还是也要特征、BPM、信号质量",
        "是否上传原始心音或波形，供服务器回看和生成报告",
        "测量过程中实时上传，还是结束后按会话批量上传",
        "AI 推理固定在本地主机运行，服务器不承担模型推理",
    ]),
]

NOTES = {
    0: "这次汇报控制在 5 分钟：先讲这几天做了什么，再讲系统三部分架构与分工，"
       "最后是需要老师确认的两件事。",
    1: "这几天我们主要做了三件事。第一，梳理了系统整体方案，把心音采集、数据传输、"
       "主机分析和结果显示这条链路理清了。第二，整理了设备端的数据接口初稿，"
       "确定 8 kHz 采集、20 ms 一帧，并加入序号、时间戳和 CRC 校验，"
       "方便后面检查丢帧和数据损坏。第三，制作了系统架构图和产品示意图，"
       "同时开始整理硬件与软件之间的接口。",
    2: "系统分成探头、主机和服务器三部分。探头贴胸口采集，通过 BLE 或有线把数据送给主机；"
       "主机负责接收、显示、播放和本地 AI 推理；服务器只负责数据存储。"
       "重点是：主机在没有服务器的情况下，也能完成采集、显示、播放和初步诊断，"
       "服务器是外部平台，不参与实时诊断，也不做模型训练和管理。",
    3: "具体职责上，探头只做可靠采集与预处理，模拟前端完成放大、20 Hz 高通、"
       "1 kHz 低通、抗混叠和偏置，由低功耗 MCU 输出 4 kHz 或 8 kHz 数据，"
       "默认 4 kHz PCM、20 ms 80 个样本、160 字节、64 kbps；"
       "主机负责接收重组、校验、窗口缓存，本地跑 int8 量化模型，"
       "驱动屏幕和扬声器，并负责 WiFi 上传、断网缓存和恢复补传；"
       "服务器只存数据，提供最基础的上传与读取接口。",
    4: "分工方面，我主要负责 AI 模型训练与边缘部署、显示、无线通信和软件集成；"
       "队友负责心音采集、模拟前端、回放、电源、PCB 和硬件结构。"
       "我们会共同冻结接口，并完成联调。",
    5: "目前已经确定采用二体式，探头贴胸采集，主机独立负责显示、控制、播放和联网。"
       "想请老师确认这个总体结构是否合适，以及探头和主机之间采用 BLE 无线还是有线连接、"
       "蓝牙是否为硬性要求，还有主机形态是手持还是随身佩戴。"
       "服务器方面，我们计划只保留数据存储功能，不参与 AI 推理、模型训练和模型管理；"
       "想请老师确认上传的数据范围，以及实时上传还是测量结束后上传。"
       "确认之后，我们再和队友细化器件、接口和上传字段。",
}


# ---------------------------------------------------------------- build
def build():
    prs = Presentation(DECK)

    # --- slide 5 kicker: 团队分工 becomes 进展 · 04 (content-addressed)
    team = find_slide(prs, "我负责")
    if not set_text(team, "进展 · 03", "进展 · 04"):
        print("note: team kicker '进展 · 03' not found (already updated?)")

    # --- slide 3: 目前方案 -> 系统总体架构
    s3 = find_slide(prs, "目前方案")
    wipe(s3)
    header(s3, "进展 · 02", "系统总体架构", dk.CORAL)
    for f in FLOW:
        rect(s3, f["x"], 1.92, f["w"], 1.0, fill=f["fill"],
             line_color=f["line"], line_w=1.75, rounded=True, radius=0.1,
             text=[{"text": f["text"], "size": f["size"], "bold": True,
                    "color": f["fg"], "align": "c"}],
             anchor="MIDDLE", pad=(0.08, 0.04, 0.08, 0.04))
    for gx, gw, label in GAPS:
        txt(s3, gx, 1.94, gw, 0.28,
            [{"text": label, "size": 11, "color": dk.MUTED, "align": "c"}])
        rect(s3, gx + gw / 2 - 0.27, 2.29, 0.54, 0.26, fill=dk.MUTED,
             shape=MSO_SHAPE.RIGHT_ARROW)
    for n, st in enumerate(STATEMENTS):
        tinted(s3, 0.9, 3.15 + n * 1.06, 5.5, 0.96, n + 1, st, size=16.5)
    rect(s3, 6.75, 3.0, 5.68, 3.3, fill=dk.WHITE, line_color=dk.CARD_LINE,
         rounded=True, radius=0.04)
    img(s3, ARCH, 6.97, 3.06, 5.2, 3.18)
    band(s3, "没有服务器也能完成初步诊断，服务器只是外部存储", dk.CORAL)
    notes(s3, NOTES[2])

    # --- slide 5 slot: 需要老师确认 -> 三部分职责 (then moved before 团队分工)
    s4 = find_slide(prs, "需要老师确认")
    wipe(s4)
    header(s4, "进展 · 03", "三部分职责", dk.TEAL)
    for (x, w), (name, accent, items) in zip(DUTY_X, DUTY):
        col_card(s4, x, 1.82, w, 4.48, accent, name, items)
    band(s4, "探头只采集，主机做决策与显示，服务器只存储", dk.TEAL)
    notes(s4, NOTES[3])

    # --- last slide: 下一步 -> 需要老师确认 (+ 下一步 band)
    s6 = find_slide(prs, "计划 · 01")   # NOT "下一步": the cover chip says 下一步 too
    wipe(s6)
    header(s6, "待确认 · 01", "需要老师确认：结构与上传范围", dk.CORAL)
    cw = (dk.CW - 0.4) / 2
    for i, (head, accent, rows) in enumerate(CONFIRM):
        confirm_card(s6, 0.9 + i * (cw + 0.4), cw, accent, head, rows)
    band(s6, "下一步：冻结二体式结构与连接 → 确定上传范围 → 与队友细化器件、接口与上传字段",
         dk.TEAL)
    notes(s6, NOTES[5])

    # --- order: [cover, progress, 架构, 三部分职责, 团队分工, 需要老师确认]
    lst = prs.slides._sldIdLst
    # content-addressed: find the sldId whose slide contains 三部分职责 marker
    moving = None
    for el in list(lst):
        sld = prs.part.related_part(el.get(qn("r:id"))).slide
        if any(sh.has_text_frame and "三部分职责" in sh.text_frame.text
               for sh in sld.shapes):
            moving = el
    assert moving is not None, "三部分职责 slide not found for reorder"
    lst.remove(moving)
    # insert before the 团队分工 slide
    for i, el in enumerate(list(lst)):
        sld = prs.part.related_part(el.get(qn("r:id"))).slide
        if any(sh.has_text_frame and "我负责" in sh.text_frame.text
               for sh in sld.shapes):
            lst.insert(i, moving)
            break
    else:
        lst.append(moving)

    # --- animation groups from FINAL geometry (content-addressed regions)
    regions = {
        1: [(0.9, 1.85, 6.45, 3.85), (6.84, 1.85, 12.43, 3.85),
            (0.9, 4.2, 6.45, 6.2), (6.84, 4.2, 12.43, 6.2)],
        3: [(0.0, 1.75, 4.5, 6.4), (4.5, 1.75, 9.1, 6.4), (9.1, 1.75, 13.4, 6.4)],
        4: [(0.0, 1.75, 4.7, 6.4), (4.7, 1.75, 8.65, 6.4), (8.65, 1.75, 13.4, 6.4)],
        5: [(0.0, 1.75, 6.7, 6.4), (6.7, 1.75, 13.4, 6.4)],
    }
    groups = {}
    for idx, slide in enumerate(prs.slides):
        rects = []
        for sh in slide.shapes:
            l = sh.left / 914400.0
            t = sh.top / 914400.0
            cx = l + sh.width / 914400.0 / 2
            cy = t + sh.height / 914400.0 / 2
            if cy < 1.75 or cy >= 6.4:
                continue
            rects.append((sh.shape_id, cx, cy))
        if idx == 2:   # 架构: click1 = flow + image card, click2-4 = statements
            g1 = [i for i, cx, cy in rects if cy <= 2.98 or cx >= 6.65]
            rest = [r for r in rects if r[0] not in set(g1)]
            bands = [(3.0, 4.18), (4.18, 5.24), (5.24, 6.4)]
            gg = [g1] + [[i for i, cx, cy in rest if a <= cy < b]
                         for a, b in bands]
        elif idx in regions:
            gg = [[i for i, cx, cy in rects if x0 <= cx < x1 and y0 <= cy < y1]
                  for x0, y0, x1, y1 in regions[idx]]
        else:
            gg = []
        gg = [g for g in gg if g]
        if gg:
            groups[str(idx)] = gg

    json.dump(groups, open(GROUPS_OUT, "w"))
    print("animation groups ->", GROUPS_OUT)
    for k in sorted(groups, key=int):
        print("  slide", int(k) + 1, [len(g) for g in groups[k]])

    try:
        prs.save(DECK)
        print("saved in place:", DECK)
    except PermissionError:
        alt = Path(str(DECK).replace(".pptx", "-v2.pptx"))
        prs.save(alt)
        print("PERMISSION ERROR (file open in PowerPoint?) -> saved to", alt)


if __name__ == "__main__":
    build()
