#!/usr/bin/env python3
"""Generate the term-relationship diagram as SVG."""
from html import escape

W, H = 1600, 1980

INK = "#1f2328"
MUTED = "#5c6672"
FAINT = "#7a8593"

C = {
    "purple": ("#EFE9F7", "#6A4C93"),
    "blue":   ("#E6F0F9", "#2F6FA8"),
    "red":    ("#FBEAE8", "#B03A2E"),
    "amber":  ("#FDF3E1", "#C77C1E"),
    "green":  ("#E7F4EC", "#2E7D5B"),
    "gray":   ("#F3F4F6", "#8A94A0"),
}

out = []


def add(s):
    out.append(s)


def text(x, y, s, size=18, fill=INK, weight="normal", anchor="start", family="Noto Sans SC, sans-serif", opacity=None, style=None):
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    o = f' opacity="{opacity}"' if opacity else ""
    st = f' font-style="{style}"' if style else ""
    add(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}"{a}{o}{st}>{escape(s)}</text>')


def box(x, y, w, h, title, sub=None, kind="gray", tsize=22, ssize=15, dash=None,
        title_dy=None, sub_lines=None):
    fill, stroke = C[kind]
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="2"{d}/>')
    cx = x + w / 2
    lines = sub_lines if sub_lines is not None else ([sub] if sub else [])
    if lines:
        total = tsize + 8 + (len(lines) - 1) * (ssize + 6)
        ty = y + h / 2 - total / 2 + tsize * 0.82
    else:
        ty = y + h / 2 + tsize * 0.34
    if title_dy:
        ty = y + title_dy
    text(cx, ty, title, size=tsize, weight="600", anchor="middle", fill=INK)
    sy = ty + ssize + 8
    for ln in lines:
        text(cx, sy, ln, size=ssize, fill=MUTED, anchor="middle")
        sy += ssize + 6


def arrow(x1, y1, x2, y2, kind="dark", dash=None, width=2.0):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    color = {"dark": "#3b434c", "muted": "#9aa3ad", "green": "#2E7D5B"}[kind]
    m = {"dark": "arwD", "muted": "arwM", "green": "arwG"}[kind]
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="{width}"{d} marker-end="url(#{m})"/>')


def curve(x1, y1, cx_, cy_, x2, y2, kind="muted", dash="6 5", width=1.8):
    color = {"dark": "#3b434c", "muted": "#9aa3ad", "green": "#2E7D5B"}[kind]
    m = {"dark": "arwD", "muted": "arwM", "green": "arwG"}[kind]
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<path d="M {x1} {y1} Q {cx_} {cy_} {x2} {y2}" fill="none" '
        f'stroke="{color}" stroke-width="{width}"{d} marker-end="url(#{m})"/>')


add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
add('<defs>')
for mid, col in (("arwD", "#3b434c"), ("arwM", "#9aa3ad"), ("arwG", "#2E7D5B")):
    add(f'<marker id="{mid}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{col}"/></marker>')
add('</defs>')
add(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

# ---------------- header ----------------
text(40, 62, "心音相关名词的层级关系与任务映射", size=36, weight="700")
text(40, 98, "同一批病人的不同层级标签；不是一套互斥的疾病分类", size=19, fill=MUTED)

# ---------------- Panel A ----------------
text(40, 152, "A · 临床链条", size=24, weight="700", fill="#2b333c")

bands = [(184, 340), (368, 792), (818, 998), (1018, 1168), (1188, 1348)]
for (t, b) in bands:
    add(f'<rect x="30" y="{t}" width="1540" height="{b-t}" rx="14" fill="#F7F9FB"/>')

labels = [(184, "① 病因 / 机制"), (368, "② 结构 / 功能病变"),
          (818, "③ 结局"), (1018, "④ 体征（听诊）"), (1188, "⑤ 信号与目标")]
for (t, s) in labels:
    text(46, t + 27, s, size=16, weight="600", fill=FAINT)

# -- row 1: causes
box(220, 206, 250, 112, "RHD", "风湿性心脏病", "purple", tsize=24, sub_lines=["链球菌感染后瓣膜损害"])
box(510, 206, 250, 112, "CHD", "先天性心脏病", "purple", tsize=24, sub_lines=["VSD / ASD / PDA / 二叶瓣"])
box(800, 206, 250, 112, "CAD", "冠心病", "purple", tsize=24, sub_lines=["冠脉粥样硬化"])
box(1090, 206, 320, 112, "高血压 / 左心疾病", None, "purple", tsize=22, sub_lines=["非瓣膜病因"])

# -- row 2: VHD container + subtypes
add('<rect x="400" y="410" width="700" height="360" rx="14" fill="#DCEBF7" '
    'stroke="#2F6FA8" stroke-width="2.5"/>')
text(418, 448, "VHD  瓣膜性心脏病", size=24, weight="700", fill="#1F4E75")
text(418, 474, "4 个亚型 + 正常 = Yaseen 的 5 类", size=15, fill="#44688A")

box(220, 470, 150, 120, "N", "正常", "gray", tsize=26, sub_lines=["Normal"])

subs = [(418, "AS", "主动脉瓣狭窄"), (585, "MS", "二尖瓣狭窄"),
        (752, "MR", "二尖瓣反流"), (919, "MVP", "二尖瓣脱垂")]
for (x, t, s) in subs:
    fill, stroke = "#FFFFFF", "#2F6FA8"
    add(f'<rect x="{x}" y="520" width="150" height="120" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>')
    text(x + 75, 562, t, size=24, weight="700", anchor="middle")
    text(x + 75, 590, s, size=14, fill=MUTED, anchor="middle")
    text(x + 75, 612, "瓣膜病变" if t != "MVP" else "结构基础", size=13, fill="#8a94a0", anchor="middle")

curve(994, 640, 900, 710, 827, 640, kind="muted", dash="6 5")
add('<rect x="836" y="700" width="150" height="24" rx="6" fill="#DCEBF7"/>')
text(911, 717, "MVP 常合并 MR", size=14, fill="#44688A", anchor="middle")

# PH
box(1160, 490, 270, 130, "PH", "肺动脉高压", "blue", tsize=26, sub_lines=["第 2 组：左心疾病（VHD / HF）"])

# coverage rect
add('<rect x="205" y="398" width="910" height="384" rx="16" fill="none" stroke="#2E7D5B" '
    'stroke-width="2.2" stroke-dasharray="9 6"/>')
text(214, 706, "Yaseen 五分类", size=14, weight="600", fill="#2E7D5B")
text(214, 724, "标签空间", size=14, weight="600", fill="#2E7D5B")

# -- row 3: HF
box(820, 850, 380, 120, "HF", "心力衰竭", "red", tsize=26,
    sub_lines=["任一病因的终末综合征", "听诊是 S3/S4、啰音，不是杂音"])
text(240, 900, "HF 常无特异性杂音", size=17, weight="600", fill="#B03A2E")
text(240, 926, "→ 杂音筛查易漏", size=16, fill=MUTED)

# -- row 4: sign
box(400, 1030, 380, 110, "杂音 murmur", "体征，不是疾病", "amber", tsize=24,
    sub_lines=["湍流产生的可闻声音"])
box(820, 1030, 360, 110, "生理性 / 非心脏来源", None, "amber", tsize=20,
    sub_lines=["儿童约 50%、贫血、甲亢、发热"])

# -- row 5: signal
box(400, 1200, 380, 110, "PCG 心音信号", "20–1000 Hz · 8 kHz · 16 bit", "green", tsize=24)
box(820, 1200, 360, 110, "BP / PAP 估计", None, "gray", tsize=22, dash="6 5",
    sub_lines=["回归目标：MAE / R²", "与疾病分类不同轴"])

# -- edges
arrow(345, 318, 620, 410)
text(352, 366, "MS 的主要病因", size=14, fill=MUTED)
arrow(635, 318, 700, 410)
text(690, 372, "二叶瓣 → AS", size=14, fill=MUTED)
arrow(925, 318, 860, 410, kind="muted", dash="6 5")
text(944, 372, "缺血性 MR", size=14, fill=MUTED)
arrow(1250, 318, 1295, 490)
text(1302, 372, "左心疾病 → 第 2 组", size=14, fill=MUTED)

arrow(470, 770, 470, 1030, width=2.6)
text(482, 806, "VHD 是杂音的主要来源", size=14, fill=MUTED)
arrow(950, 770, 950, 850)
arrow(1100, 560, 1160, 560, kind="muted", dash="6 5")
arrow(820, 1085, 780, 1085, kind="muted", dash="6 5")
arrow(590, 1140, 590, 1200, width=2.6)
arrow(780, 1255, 820, 1255, kind="muted", dash="6 5")
text(800, 1244, "回归", size=13, fill="#8a94a0", anchor="middle")

# -- footnote + legend
text(40, 1378, "注：AS 的杂音是 often 不是 always；约 50% 儿童有生理性杂音；反向（有杂音 ⇒ VHD）不成立。",
     size=15, fill=MUTED)
add('<line x1="40" y1="1400" x2="88" y2="1400" stroke="#3b434c" stroke-width="2" marker-end="url(#arwD)"/>')
text(98, 1406, "主要 / 常见路径", size=14, fill=MUTED)
add('<line x1="300" y1="1400" x2="348" y2="1400" stroke="#9aa3ad" stroke-width="1.8" stroke-dasharray="6 5" marker-end="url(#arwM)"/>')
text(358, 1406, "次要 / 间接 / 非因果", size=14, fill=MUTED)
add('<rect x="580" y="1391" width="42" height="18" rx="4" fill="none" stroke="#2E7D5B" stroke-width="2" stroke-dasharray="6 4"/>')
text(632, 1406, "Yaseen 五分类覆盖范围", size=14, fill=MUTED)

# ---------------- Panel B ----------------
text(40, 1470, "B · 任务与负类：为什么不能合成一个分类器", size=24, weight="700", fill="#2b333c")

cards = [
    (40, "① 杂音检测", "二分类 · 体征", "amber",
     [("正类", ["任何杂音（生理性 + 病理性）"]),
      ("负类", ["无杂音：正常、无杂音病理、伪迹"]),
      ("数据", ["CirCor / CinC2016", "CinC2022 / PASCAL"]),
      ("指标", ["AUROC、敏感度、特异度"]),
      ("用途", ["社区筛查；文献 49 篇"])]),
    (550, "② 正常 / 异常分流", "二分类 · 派生", "gray",
     [("正类", ["任一 VHD"]),
      ("负类", ["仅正常"]),
      ("数据", ["Yaseen（同一份划分）"]),
      ("指标", ["AUROC；不得称“杂音检测”"]),
      ("用途", ["对照；证明数据受限"])]),
    (1060, "③ VHD 亚型分类", "五分类 · 疾病", "blue",
     [("正类", ["AS / MS / MR / MVP"]),
      ("负类", ["仅正常"]),
      ("数据", ["Yaseen"]),
      ("指标", ["macro F1 + 混淆矩阵"]),
      ("用途", ["FYP 主线（ADR-020）"])]),
]

for (x, title, badge, kind, rows) in cards:
    fill, stroke = C[kind]
    add(f'<rect x="{x}" y="1492" width="480" height="352" rx="12" fill="#ffffff" stroke="{stroke}" stroke-width="2"/>')
    add(f'<rect x="{x}" y="1492" width="480" height="56" rx="12" fill="{fill}"/>')
    add(f'<rect x="{x}" y="1528" width="480" height="20" fill="{fill}"/>')
    text(x + 20, 1528, title, size=23, weight="700")
    text(x + 460, 1528, badge, size=14, fill=MUTED, anchor="end")
    y = 1590
    for (k, lines) in rows:
        text(x + 20, y, k, size=15, weight="600", fill=FAINT)
        for i, ln in enumerate(lines):
            text(x + 86, y + i * 23, ln, size=16, fill=INK)
        y += 54

# warning strip
add('<rect x="40" y="1866" width="1500" height="76" rx="12" fill="#FBEAE8" stroke="#B03A2E" stroke-width="2"/>')
text(62, 1898, "⚠ 闭集五分类没有“其他”出口：CHD 的 VSD 会被判成 MR（声学相似），无杂音的 HF 会被判成 N。→ 部署前必须加开放集拒绝。",
     size=17, weight="600", fill="#8C2D22")
text(62, 1926, "⚠ 三个任务的标签集不同：① 的正类含生理性杂音与非 VHD 病理，②③ 的正类只有 VHD、负类只有正常 → 数字不可横比（ADR-021）。",
     size=16, fill="#8C2D22")

add('</svg>')

with open("/home/psr/FYP/assets/fig-term-relations.svg", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("svg written")
