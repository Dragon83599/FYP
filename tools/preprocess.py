#!/usr/bin/env python3
"""W1 预处理流水线：Yaseen 1,000 条 → manifest / windows / splits / quality。

执行手册: docs/workflow.md §2
开工清单: docs/tasks/w1-preprocess-checklist.md
参数出处: docs/parameter-ledger.md（**本文件不得擅自改参数**）
决策记录: ADR-002/020/029/031/034（原有）+ ADR-035/036/037/038（本次实施新增）

一条命令重跑全部产物::

    .venv/bin/python tools/preprocess.py all

只重算某一个阶段::

    .venv/bin/python tools/preprocess.py extract     # 解包 + 校验
    .venv/bin/python tools/preprocess.py probe-filter  # 带通实现位置对比（ADR-036 证据）
    .venv/bin/python tools/preprocess.py figures       # 目视验收图（不影响数据哈希）
    .venv/bin/python tools/preprocess.py verify        # 重跑并比较哈希（可复现性验收）

产物（路径按 w1-preprocess-checklist.md §0）::

    data/processed/manifest.csv
    data/processed/windows.csv
    data/processed/splits.json
    data/processed/quality.csv
    data/processed/windows.npy       # (n_windows, 4000) float32，z-score 后
    data/processed/summary.json      # 参数快照 + 断言 + 各产物 sha256
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pathlib
import sys
import wave

import numpy as np
from scipy import signal

# ---------------------------------------------------------------------------
# 冻结参数（出处见 docs/parameter-ledger.md 的 B 节；改动必须走 ADR）
# ---------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 类别顺序与标签索引（ADR-037 冻结）：AS=0, MS=1, MR=2, MVP=3, N=4
CLASSES = ["AS", "MS", "MR", "MVP", "N"]
LABEL_INDEX = {c: i for i, c in enumerate(CLASSES)}

FS_RAW = 8000          # 原始采样率【文献 01】
FS = 2000              # 统一重采样目标【文献 20/15】+【ADR-029】
DECIM = FS_RAW // FS   # = 4，多相 FIR 抗混叠抽取

# 端点检测（全部【E4】，parameter-ledger B 节）
EP_FRAME_MS = 25
EP_HOP_MS = 10
EP_NOISE_PERCENTILE = 10
EP_THRESHOLD_FACTOR = 3.0
EP_MIN_VOICED_S = 0.5

# 带通（【文献 15/22/27】频段 +【ADR-002】固定 +【ADR-034】零相位）
BAND_HZ = (20.0, 1000.0)
FILTER_ORDER = 4       # scipy/MATLAB 的 butter(N=4, bandpass) → 8 极点 / 4 个二阶节
# ADR-036：20–1000 Hz 的上边沿在 2 kHz 上等于 Nyquist，数字滤波器不可实现，
# 因此在 8 kHz（原始采样率、ADR-002 的采集率）上做零相位带通，再做 1/4 抽取。
FILTER_STAGE_FS = FS_RAW

# 归一化【文献 21】：每窗独立 z-score
ZSCORE_PER_WINDOW = True

# 切窗【文献 17】2 s +【E4】1 s 步进 +【E4】两端补零
WIN_S = 2.0
HOP_S = 1.0
WIN_N = int(round(WIN_S * FS))    # 4000
HOP_N = int(round(HOP_S * FS))    # 2000

# 质量指标 BER【文献 26】的频带（w1-checklist §7）
BER_TARGET_HZ = (20.0, 700.0)
BER_NOISE_HZ = ((0.0, 20.0), (700.0, 1000.0))
BER_NPERSEG = 1024

# 划分【ADR-031】+ w1-checklist §6
SPLIT_SEED = 42
N_FOLDS = 5
HOLDOUT_PER_CLASS = 30      # 150 条 = 15%
SPLIT_CREATED = "2026-10-30"  # 固定串（不用运行时时间），否则重跑哈希会变

# 路径
RAW_DIR = ROOT / "data" / "raw" / "yaseen"
SRC_DIR = RAW_DIR / "src"
EXTRACT_DIR = RAW_DIR / "extracted"
OUT_DIR = ROOT / "data" / "processed"
FIG_DIR = ROOT / "data" / "reports" / "w1"

# 五个类别压缩包的 git blob SHA-1（完整性校验；见 ADR-035）
ARCHIVE_BLOB_SHA1 = {
    "AS": "415f7ae3836459e833b8e67973087d7901f5003a",
    "MS": "5aab1ab1160207e6776ceacbd81f6f0f73e4fc78",
    "MR": "e6ad8d79875691134987e6f0d9c9b71652a7c7fe",
    "MVP": "b20050551c3f956e4fd430f440991eb69d4636a9",
    "N": "b3800d93b5199f8c21afbc2dd83ea5f0020ac5de",
}

PIPELINE_VERSION = "w1.0"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha1(path: pathlib.Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def read_wav_i16(path: pathlib.Path) -> np.ndarray:
    """读 PCM16 单声道 wav → float64 in [-1, 1)。头不合法直接抛错（不静默修正）。"""
    with wave.open(str(path), "rb") as w:
        ch, sw, fs, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        if (ch, sw, fs) != (1, 2, FS_RAW):
            raise ValueError(f"{path.name}: 期望 1ch/16bit/{FS_RAW}Hz，实际 {ch}ch/{sw*8}bit/{fs}Hz")
        raw = w.readframes(n)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if x.size != n:
        raise ValueError(f"{path.name}: 读到的样本数 {x.size} != 头声明 {n}")
    return x


def frame_energy(x: np.ndarray, frame: int, hop: int) -> np.ndarray:
    """25 ms 帧 / 10 ms 移 的短时能量（每帧均方值）。"""
    if x.size < frame:
        return np.empty(0)
    n_frames = 1 + (x.size - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n_frames)[:, None]
    return (x[idx] ** 2).mean(axis=1)


def endpoint_detect(x: np.ndarray) -> dict:
    """端点检测：噪声底 = 能量序列 10 分位；阈值 = 噪声底 × 3；剪首尾低于阈值的帧。

    返回裁剪区间与诊断量；**不丢弃任何录音**，异常只做标记（ADR-037）。
    """
    frame = int(round(EP_FRAME_MS * 1e-3 * FS_RAW))
    hop = int(round(EP_HOP_MS * 1e-3 * FS_RAW))
    e = frame_energy(x, frame, hop)
    info = dict(
        ep_frame_samples=frame,
        ep_hop_samples=hop,
        ep_n_frames=int(e.size),
        ep_noise_floor=float("nan"),
        ep_threshold=float("nan"),
        ep_anomaly=False,
        ep_reason="",
        start=0,
        end=int(x.size),
    )
    if e.size == 0:
        info.update(ep_anomaly=True, ep_reason="short_signal")
        return info

    floor = float(np.percentile(e, EP_NOISE_PERCENTILE))
    thr = EP_THRESHOLD_FACTOR * floor
    info["ep_noise_floor"] = floor
    info["ep_threshold"] = thr

    above = np.flatnonzero(e > thr)
    if above.size == 0:
        # 全静音 / 阈值不可分：保留整段，标记人工检查
        info.update(ep_anomaly=True, ep_reason="all_below_threshold")
        return info

    start = int(above[0]) * hop
    end = int(above[-1]) * hop + frame
    end = min(end, int(x.size))
    info["start"], info["end"] = start, end
    if (end - start) / FS_RAW < EP_MIN_VOICED_S:
        info.update(ep_anomaly=True, ep_reason="trimmed_lt_0.5s")
    return info


def design_bandpass() -> np.ndarray:
    """4 阶 Butterworth 20–1000 Hz，SOS 形式，在 8 kHz 上设计（ADR-036）。"""
    return signal.butter(
        FILTER_ORDER, [BAND_HZ[0], BAND_HZ[1]], btype="bandpass",
        fs=FILTER_STAGE_FS, output="sos",
    )


def bandpass_zero_phase(x: np.ndarray, sos: np.ndarray) -> np.ndarray:
    """零相位滤波（sosfiltfilt，前后各滤一次）——ADR-034。部署端必须同实现。"""
    padlen = min(3 * (2 * len(sos) + 1), max(0, x.size - 1))
    return signal.sosfiltfilt(sos, x, padtype="odd", padlen=padlen)


def resample_to_2k(x: np.ndarray) -> np.ndarray:
    """多相 FIR 抗混叠抽取 8 kHz → 2 kHz（up=1/down=4）。"""
    return signal.resample_poly(x, up=1, down=DECIM, window=("kaiser", 5.0))


def enumerate_windows(n: int) -> list[tuple[int, int, int, int]]:
    """窗枚举：0 起、步进 1 s 的满窗；不足 2 s 两端补零；尾部未被覆盖时补一个尾对齐窗。

    返回 [(start, end, pad_left, pad_right)]，坐标都在 2 kHz、端点检测之后的信号上。
    规则与理由见 ADR-037（含 why-not 只保留步进窗）。
    """
    if n <= WIN_N:
        pad = WIN_N - n
        left = pad // 2
        return [(0, n, left, pad - left)]
    starts = [0]
    while starts[-1] + HOP_N + WIN_N <= n:
        starts.append(starts[-1] + HOP_N)
    if n - (starts[-1] + WIN_N) > 0:
        starts.append(n - WIN_N)
    return [(s, s + WIN_N, 0, 0) for s in starts]


def band_power(f: np.ndarray, pxx: np.ndarray, lo: float, hi: float) -> float:
    """Welch PSD 在 [lo, hi) 上的积分功率（梯形近似）。"""
    m = (f >= lo) & (f < hi)
    if not np.any(m):
        return 0.0
    return float(np.trapezoid(pxx[m], f[m]))


def ber_of(segment: np.ndarray) -> tuple[float, float, float, float]:
    """BER = 目标带(20–700 Hz)功率 ÷ 噪声带(0–20 与 700–1000 Hz)功率【文献 26】。

    在**重采样后、带通前**的窗上计算（ADR-038）：带通会把噪声带本身滤掉，
    滤完再算等于测滤波器，测不到录音质量。
    """
    f, pxx = signal.welch(
        segment.astype(np.float64), fs=FS, window="hann",
        nperseg=min(BER_NPERSEG, segment.size), noverlap=None, detrend="constant",
        scaling="density",
    )
    target = band_power(f, pxx, *BER_TARGET_HZ)
    noise = sum(band_power(f, pxx, *b) for b in BER_NOISE_HZ)
    if noise <= 0.0:
        ratio = math.inf if target > 0.0 else float("nan")
    else:
        ratio = target / noise
    return ratio, target, band_power(f, pxx, *BER_NOISE_HZ[0]), band_power(f, pxx, *BER_NOISE_HZ[1])


def zscore(w: np.ndarray) -> tuple[np.ndarray, bool]:
    mu = float(w.mean())
    sd = float(w.std())
    if not np.isfinite(sd) or sd < 1e-12:
        return w - mu, True
    return (w - mu) / sd, False


# ---------------------------------------------------------------------------
# 阶段 1：解包 + 读取清单
# ---------------------------------------------------------------------------


def stage_extract() -> list[dict]:
    """校验压缩包 SHA-1（git blob），解包到 data/raw/yaseen/extracted/<CLASS>/。"""
    import libarchive  # 延迟导入：只有解包阶段需要

    if not SRC_DIR.exists():
        raise SystemExit(f"缺少 {SRC_DIR}：需要先把 5 个类别 .rar 放到那里（见 ADR-035）")

    rows = []
    for cls in CLASSES:
        arc = SRC_DIR / f"{cls}.rar"
        if not arc.exists():
            raise SystemExit(f"缺少 {arc}")
        got = git_blob_sha1(arc)
        want = ARCHIVE_BLOB_SHA1[cls]
        if got != want:
            raise SystemExit(f"{arc.name} 内容校验失败：git-blob-sha1 {got} != {want}")
        out_dir = EXTRACT_DIR / cls
        out_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        with libarchive.file_reader(str(arc)) as archive:
            for entry in archive:
                if not entry.pathname.lower().endswith(".wav"):
                    continue
                name = pathlib.PurePath(entry.pathname).name
                target = out_dir / name
                data = b"".join(entry.get_blocks())
                # 已存在且字节相同则不重写（幂等）
                if not (target.exists() and target.stat().st_size == len(data)):
                    target.write_bytes(data)
                n += 1
        rows.append(dict(cls=cls, archive=arc.name, blob_sha1=got, n_wav=n, bytes=arc.stat().st_size))
        print(f"  {cls}: {n} 个 wav（{arc.name}, {arc.stat().st_size/1e6:.1f} MB）")

    total = sum(r["n_wav"] for r in rows)
    print(f"解包总数：{total}")
    if total != 1000:
        raise SystemExit(f"断言失败：解包 wav 总数 {total} != 1000")
    for r in rows:
        if r["n_wav"] != 200:
            raise SystemExit(f"断言失败：{r['cls']} 有 {r['n_wav']} 条 != 200")
    (RAW_DIR / "provenance.json").write_text(
        json.dumps(
            {
                "source_repo": "https://github.com/yaseen21khan/Classification-of-Heart-Sound-Signal-Using-Multiple-Features-",
                "source_branch": "master",
                "acquired_via": "GitHub blobs API (Accept: application/vnd.github.raw)",
                "acquired": SPLIT_CREATED,
                "integrity": "git blob sha1 == repo tree sha (bit-exact)",
                "archives": rows,
                "total_wav": total,
                "licence_note": "仓库无 LICENSE；音频为教学 CD/网站来源，学术可用、不得再分发（ADR-020 风险 1）",
            },
            ensure_ascii=False, indent=2, sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return rows


def scan_manifest() -> list[dict]:
    """扫描解包目录 → 每条录音一行（校验 8 kHz / 单声道 / 16 bit / 可读）。"""
    rows = []
    for cls in CLASSES:
        files = sorted((EXTRACT_DIR / cls).glob("*.wav"))
        if len(files) != 200:
            raise SystemExit(f"断言失败：{cls} 目录下有 {len(files)} 个 wav != 200")
        for p in files:
            with wave.open(str(p), "rb") as w:
                ch, sw, fs, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if (ch, sw, fs) != (1, 2, FS_RAW):
                raise SystemExit(f"{p.name}: 头不合法 {ch}ch/{sw*8}bit/{fs}Hz")
            # New_AS_001.wav → AS_001
            stem = p.stem.replace("New_", "")
            if not stem.startswith(cls + "_"):
                raise SystemExit(f"{p.name}: 文件名与类别不符")
            rows.append(
                dict(
                    recording_id=stem,
                    path=str(p.relative_to(ROOT)),
                    label=cls,
                    label_index=LABEL_INDEX[cls],
                    duration_s=n / fs,
                    fs=fs,
                    n_samples=n,
                )
            )
    ids = [r["recording_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("断言失败：recording_id 不唯一")
    counts = {c: sum(1 for r in rows if r["label"] == c) for c in CLASSES}
    print(f"清单：{len(rows)} 条；各类 {counts}")
    if len(rows) != 1000:
        raise SystemExit(f"断言失败：清单 {len(rows)} 行 != 1000")
    for c, k in counts.items():
        if k != 200:
            raise SystemExit(f"断言失败：{c} 有 {k} 条 != 200")
    return rows


# ---------------------------------------------------------------------------
# 阶段 2：处理（端点 → 重采样 → 带通 → 切窗 → z-score → BER）
# ---------------------------------------------------------------------------


def stage_process(manifest: list[dict], splits: dict) -> tuple[list[dict], list[dict], dict]:
    sos = design_bandpass()
    win_rows: list[dict] = []
    qual_rows: list[dict] = []
    diag = dict(
        anomalies=[],
        trim_gain_s=[],
        cvt_energy_retained=[],
        pad_ratio=[],
        zero_var_windows=0,
        alias_db=[],
        resampled_len_delta=[],
        max_abs=0.0,
    )
    split_of = {}
    for rid in splits["holdout"]:
        split_of[rid] = ("holdout", -1)
    for fi, fold in enumerate(splits["folds"]):
        for rid in fold:
            split_of[rid] = (f"fold{fi}", fi)

    total_windows = 0
    for i, rec in enumerate(manifest):
        x = read_wav_i16(ROOT / rec["path"])
        diag["max_abs"] = max(diag["max_abs"], float(np.max(np.abs(x))) if x.size else 0.0)
        ep = endpoint_detect(x)
        seg = x[ep["start"]:ep["end"]]
        if ep["ep_anomaly"]:
            diag["anomalies"].append(dict(recording_id=rec["recording_id"], reason=ep["ep_reason"]))
        # 保留的能量比例（客观替代"人工比对起止"的过切检查）
        total_e = float(np.sum(x ** 2))
        kept_e = float(np.sum(seg ** 2))
        diag["trim_gain_s"].append((len(x) - len(seg)) / FS_RAW)
        diag["cvt_energy_retained"].append(kept_e / total_e if total_e > 0 else float("nan"))

        # 带通在 8 kHz 上做（ADR-036），随后 1/4 抽取
        y8 = bandpass_zero_phase(seg, sos)
        y2 = resample_to_2k(y8)
        # BER 用**带通前**的 2 kHz 信号（ADR-038）：带通会把噪声带本身滤掉，
        # 滤完再算等于测滤波器而不是测录音质量。
        y2_raw = resample_to_2k(seg)
        diag["resampled_len_delta"].append(len(y2) - len(seg) / DECIM)

        # 抗混叠证据：带通后 8 kHz 信号的 >1.02 kHz 功率应远低于 20–1000 Hz
        if i % 100 == 0:
            f8, p8 = signal.welch(y8, fs=FS_RAW, nperseg=min(1024, y8.size))
            hi = float(np.trapezoid(p8[(f8 >= 1020) & (f8 < 3990)], f8[(f8 >= 1020) & (f8 < 3990)]))
            lo = float(np.trapezoid(p8[(f8 >= 20) & (f8 < 1000)], f8[(f8 >= 20) & (f8 < 1000)]))
            diag["alias_db"].append(10 * math.log10(max(hi, 1e-30) / max(lo, 1e-30)))

        split, fold = split_of[rec["recording_id"]]
        n_w = 0
        for wi, (s, e, pl, pr) in enumerate(enumerate_windows(len(y2))):
            raw_seg = y2[s:e]
            pads = np.pad(raw_seg, (pl, pr), mode="constant")
            if pads.size != WIN_N:
                raise SystemExit(f"{rec['recording_id']} w{wi}: 窗长 {pads.size} != {WIN_N}")
            # BER 用同一段窗，但取带通前的信号（ADR-038）
            pads_pre = np.pad(y2_raw[s:e], (pl, pr), mode="constant")
            ber, tp, nl, nh = ber_of(pads_pre)
            # 带通已在 8 kHz 上做过（ADR-036），这里不再重复滤波
            w, zero_var = zscore(pads)
            if zero_var:
                diag["zero_var_windows"] += 1
            pad_ratio = (pl + pr) / WIN_N
            diag["pad_ratio"].append(pad_ratio)
            win_rows.append(dict(
                recording_id=rec["recording_id"], window_idx=wi,
                start_sample=s, end_sample=e, n_samples=e - s,
                padded=int(pl + pr > 0), pad_left=pl, pad_right=pr, pad_ratio=pad_ratio,
                label=rec["label"], label_index=rec["label_index"],
                split=split, fold=fold,
            ))
            qual_rows.append(dict(
                recording_id=rec["recording_id"], window_idx=wi,
                ber=ber, target_power=tp, noise_low_power=nl, noise_high_power=nh,
                zero_variance=int(zero_var),
            ))
            win_rows[-1]["_data"] = w.astype(np.float32)
            n_w += 1
        rec["n_windows"] = n_w
        rec["trim_start_s"] = ep["start"] / FS_RAW
        rec["trim_end_s"] = ep["end"] / FS_RAW
        rec["duration_after_trim_s"] = (ep["end"] - ep["start"]) / FS_RAW
        rec["anomaly"] = int(ep["ep_anomaly"])
        rec["anomaly_reason"] = ep["ep_reason"]
        rec["split"] = split
        rec["fold"] = fold
        total_windows += n_w

    diag["n_windows"] = total_windows
    diag["pad_ratio_q"] = {
        q: float(np.percentile(diag["pad_ratio"], q)) for q in (50, 90, 95, 99, 100)
    }
    diag["frac_windows_padded"] = float(np.mean([1 if r["pad_ratio"] > 0 else 0 for r in win_rows]))
    return win_rows, qual_rows, diag


# ---------------------------------------------------------------------------
# 阶段 3：划分索引
# ---------------------------------------------------------------------------


def build_splits(manifest: list[dict]) -> dict:
    """录音级分层划分：先 holdout 30/类，余 170/类 分 5 折（34/类/折）。"""
    by_class: dict[str, list[str]] = {c: [] for c in CLASSES}
    for r in manifest:
        by_class[r["label"]].append(r["recording_id"])

    holdout: list[str] = []
    folds: list[list[str]] = [[] for _ in range(N_FOLDS)]
    for ci, cls in enumerate(CLASSES):
        rids = sorted(by_class[cls])
        rng = np.random.default_rng([SPLIT_SEED, ci])
        perm = [rids[i] for i in rng.permutation(len(rids))]
        holdout.extend(perm[:HOLDOUT_PER_CLASS])
        rest = perm[HOLDOUT_PER_CLASS:]
        per_fold = len(rest) // N_FOLDS
        for fi in range(N_FOLDS):
            folds[fi].extend(rest[fi * per_fold:(fi + 1) * per_fold])

    return dict(
        seed=SPLIT_SEED,
        holdout=sorted(holdout),
        folds=[sorted(f) for f in folds],
        created=SPLIT_CREATED,
        note="recording-level, stratified by class; holdout 30/class then 5 folds of 34/class "
             "per class via numpy default_rng([seed, class_index]); windows inherit their "
             "recording's split",
        n_recordings=len(manifest),
        class_order=CLASSES,
        holdout_per_class=HOLDOUT_PER_CLASS,
        folds_per_class=len(by_class[CLASSES[0]]) - HOLDOUT_PER_CLASS,
        counts={
            "holdout": {c: sum(1 for r in holdout if r.split("_")[0] == c) for c in CLASSES},
            "folds": [
                {c: sum(1 for r in f if r.split("_")[0] == c) for c in CLASSES} for f in folds
            ],
        },
    )


def assert_splits(splits: dict, manifest: list[dict]) -> dict:
    """四条验收（w1-checklist §6）：无泄漏 / 分层 / 覆盖 / 可复现（后者由 verify 检查）。"""
    all_rids = [r["recording_id"] for r in manifest]
    hold = splits["holdout"]
    folds = splits["folds"]
    flat = hold + [r for f in folds for r in f]

    res = {}
    # 1) 无泄漏
    res["no_leak"] = len(flat) == len(set(flat))
    # 2) 覆盖
    res["coverage"] = set(flat) == set(all_rids) and len(flat) == len(all_rids)
    # 3) 分层（每折每类 34±2；holdout 每类 30±2）
    per_fold_ok = all(
        34 - 2 <= splits["counts"]["folds"][fi][c] <= 34 + 2
        for fi in range(len(folds)) for c in CLASSES
    )
    hold_ok = all(30 - 2 <= splits["counts"]["holdout"][c] <= 30 + 2 for c in CLASSES)
    res["stratified"] = bool(per_fold_ok and hold_ok)
    res["holdout_size"] = len(hold)
    res["fold_sizes"] = [len(f) for f in folds]
    res["holdout_per_class"] = splits["counts"]["holdout"]
    res["folds_per_class"] = splits["counts"]["folds"]

    if not (res["no_leak"] and res["coverage"] and res["stratified"]):
        raise SystemExit(f"划分断言失败：{res}")
    return res


# ---------------------------------------------------------------------------
# 阶段 4：写产物
# ---------------------------------------------------------------------------


def write_csv(path: pathlib.Path, header: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow([r[h] for h in header])


def fmt(x: float, nd: int = 6) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and not math.isfinite(x):
        return "inf" if x > 0 else "nan"
    return f"{x:.{nd}f}"


def stage_write(manifest, win_rows, qual_rows, splits, diag, split_assert) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    man_export = []
    for r in manifest:
        man_export.append(dict(
            recording_id=r["recording_id"], path=r["path"], label=r["label"],
            duration_s=fmt(r["duration_s"], 6), fs=r["fs"], n_samples=r["n_samples"],
            n_windows=r["n_windows"], label_index=r["label_index"],
            split=r["split"], fold=r["fold"],
            duration_after_trim_s=fmt(r["duration_after_trim_s"], 6),
            trim_start_s=fmt(r["trim_start_s"], 6), trim_end_s=fmt(r["trim_end_s"], 6),
            anomaly=r["anomaly"], anomaly_reason=r["anomaly_reason"],
        ))
    write_csv(OUT_DIR / "manifest.csv", [
        "recording_id", "path", "label", "duration_s", "fs", "n_samples", "n_windows",
        "label_index", "split", "fold", "duration_after_trim_s", "trim_start_s",
        "trim_end_s", "anomaly", "anomaly_reason",
    ], man_export)

    win_export = []
    for r in win_rows:
        win_export.append(dict(
            recording_id=r["recording_id"], window_idx=r["window_idx"],
            start_sample=r["start_sample"], end_sample=r["end_sample"],
            n_samples=r["n_samples"], padded=r["padded"],
            pad_left=r["pad_left"], pad_right=r["pad_right"],
            pad_ratio=fmt(r["pad_ratio"], 6), label=r["label"],
            label_index=r["label_index"], split=r["split"], fold=r["fold"],
        ))
    write_csv(OUT_DIR / "windows.csv", [
        "recording_id", "window_idx", "start_sample", "end_sample", "n_samples", "padded",
        "pad_left", "pad_right", "pad_ratio", "label", "label_index", "split", "fold",
    ], win_export)

    qual_export = []
    for r in qual_rows:
        qual_export.append(dict(
            recording_id=r["recording_id"], window_idx=r["window_idx"],
            ber=fmt(r["ber"], 6), target_power=fmt(r["target_power"], 12),
            noise_low_power=fmt(r["noise_low_power"], 12),
            noise_high_power=fmt(r["noise_high_power"], 12),
            zero_variance=r["zero_variance"],
        ))
    write_csv(OUT_DIR / "quality.csv", [
        "recording_id", "window_idx", "ber", "target_power", "noise_low_power",
        "noise_high_power", "zero_variance",
    ], qual_export)

    data = np.stack([r["_data"] for r in win_rows]).astype(np.float32)
    np.save(OUT_DIR / "windows.npy", data)

    (OUT_DIR / "splits.json").write_text(
        json.dumps(splits, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    ber = np.array([r["ber"] for r in qual_rows], dtype=float)
    ber_f = ber[np.isfinite(ber)]
    n_w = len(win_rows)
    if not (1500 <= n_w <= 2000):
        raise SystemExit(f"验收失败：窗总数 {n_w} 不在 1,500–2,000")
    per_rec = np.array([r["n_windows"] for r in manifest])
    if per_rec.min() < 1 or per_rec.max() > 3:
        raise SystemExit(f"验收失败：每条录音窗数 {per_rec.min()}–{per_rec.max()} 不在 1–3")

    per_class = {c: int(np.sum([1 for r in win_rows if r["label"] == c])) for c in CLASSES}
    summary = dict(
        pipeline_version=PIPELINE_VERSION,
        generated=SPLIT_CREATED,
        params=dict(
            fs_raw=FS_RAW, fs=FS, decim=DECIM,
            endpoint=dict(frame_ms=EP_FRAME_MS, hop_ms=EP_HOP_MS,
                          noise_percentile=EP_NOISE_PERCENTILE,
                          threshold_factor=EP_THRESHOLD_FACTOR, min_voiced_s=EP_MIN_VOICED_S),
            bandpass=dict(hz=list(BAND_HZ), order=FILTER_ORDER, implement="sosfiltfilt",
                          design_fs=FILTER_STAGE_FS, sos_sections=int(design_bandpass().shape[0])),
            window=dict(win_s=WIN_S, hop_s=HOP_S, win_n=WIN_N, hop_n=HOP_N,
                        pad="zeros both ends", enumeration="stride + tail-aligned"),
            zscore="per window",
            ber=dict(target_hz=list(BER_TARGET_HZ), noise_hz=[list(b) for b in BER_NOISE_HZ],
                     method="welch", nperseg=BER_NPERSEG, domain="pre-bandpass 2 kHz"),
            split=dict(seed=SPLIT_SEED, folds=N_FOLDS, holdout_per_class=HOLDOUT_PER_CLASS),
            class_order=CLASSES,
        ),
        counts=dict(
            recordings=len(manifest), windows=n_w, per_class_windows=per_class,
            per_class_recordings={c: 200 for c in CLASSES},
            recordings_with_anomaly=len(diag["anomalies"]),
            frac_windows_padded=round(diag["frac_windows_padded"], 4),
            zero_variance_windows=diag["zero_var_windows"],
        ),
        window_stats=dict(
            per_recording_hist={str(k): int(v) for k, v in
                                zip(*np.unique(per_rec, return_counts=True))},
            pad_ratio_percentiles=diag["pad_ratio_q"],
            mean_windows_per_recording=round(float(per_rec.mean()), 4),
        ),
        endpoint_stats=dict(
            trimmed_seconds_median=round(float(np.median(diag["trim_gain_s"])), 4),
            trimmed_seconds_p95=round(float(np.percentile(diag["trim_gain_s"], 95)), 4),
            energy_retained_median=round(float(np.median(diag["cvt_energy_retained"])), 6),
            energy_retained_min=round(float(np.min(diag["cvt_energy_retained"])), 6),
            anomalies=diag["anomalies"][:20],
        ),
        aliasing=dict(
            n_checked=len(diag["alias_db"]),
            worst_db=round(float(np.max(diag["alias_db"])), 2),
            median_db=round(float(np.median(diag["alias_db"])), 2),
            note="bandpassed 8 kHz signal, power >1.02 kHz relative to 20–1000 Hz (dB)",
        ),
        resample_len_delta=dict(
            max_abs=int(np.max(np.abs(diag["resampled_len_delta"]))),
            note="resampled length minus len/4 samples (should be 0)",
        ),
        ber_stats=dict(
            median=round(float(np.median(ber_f)), 4),
            quantiles={f"q{int(q*100)}": round(float(np.quantile(ber_f, q)), 4)
                       for q in (0.05, 0.10, 0.20, 0.50, 0.80, 0.90, 0.95)},
            n_nonfinite=int(np.sum(~np.isfinite(ber))),
        ),
        split_assertions=split_assert,
        artifacts={},
        env={},
    )
    try:
        import scipy
        summary["env"] = dict(python=sys.version.split()[0], numpy=np.__version__,
                              scipy=scipy.__version__)
    except Exception:  # pragma: no cover
        pass

    for name in ["manifest.csv", "windows.csv", "quality.csv", "splits.json", "windows.npy"]:
        p = OUT_DIR / name
        summary["artifacts"][name] = dict(sha256=sha256_file(p), bytes=p.stat().st_size)

    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


# ---------------------------------------------------------------------------
# 命令：all / extract / probe-filter / figures / verify
# ---------------------------------------------------------------------------


def cmd_all(args) -> None:
    if not (EXTRACT_DIR / CLASSES[0]).exists() or args.reextract:
        print("[1/4] 解包 + 校验")
        stage_extract()
    elif args.verify_extract:
        print("[1/4] 解包目录已存在，重跑校验（不重写）")
        stage_extract()
    else:
        print("[1/4] 解包目录已存在，跳过（--reextract 强制重来，--verify-extract 只重校验）")

    print("[2/4] 扫描清单（1000 条 / 5×200）")
    manifest = scan_manifest()

    print("[3/4] 划分索引（录音级、分层、seed=%d）" % SPLIT_SEED)
    splits = build_splits(manifest)
    split_assert = assert_splits(splits, manifest)

    print("[4/4] 端点检测 → 2 kHz → 20–1000 Hz 零相位 → 切窗 → z-score → BER")
    win_rows, qual_rows, diag = stage_process(manifest, splits)

    summary = stage_write(manifest, win_rows, qual_rows, splits, diag, split_assert)

    print("\n=== W1 产物 ===")
    for k, v in summary["artifacts"].items():
        print(f"  {k:14s} {v['bytes']:>10d} B  sha256 {v['sha256'][:16]}…")
    print(f"  录音 {summary['counts']['recordings']}，窗 {summary['counts']['windows']}，"
          f"每类窗数 {summary['counts']['per_class_windows']}")
    print(f"  每条录音窗数分布 {summary['window_stats']['per_recording_hist']}；"
          f"补零窗占比 {summary['counts']['frac_windows_padded']:.3f}")
    print(f"  BER 中位 {summary['ber_stats']['median']:.2f}，"
          f"p10 {summary['ber_stats']['quantiles']['q10']:.2f}，"
          f"p20 {summary['ber_stats']['quantiles']['q20']:.2f}")
    print(f"  端点：平均裁掉 {summary['endpoint_stats']['trimmed_seconds_median']:.3f} s（中位），"
          f"能量保留中位 {summary['endpoint_stats']['energy_retained_median']:.4f}")
    print(f"  抗混叠：>1.02 kHz 相对功率最差 {summary['aliasing']['worst_db']:.1f} dB")
    print(f"  划分断言：{summary['split_assertions']}")
    if summary["endpoint_stats"]["anomalies"]:
        print(f"  ⚠ 异常录音 {summary['counts']['recordings_with_anomaly']} 条，需人工检查")


def cmd_extract(args) -> None:
    stage_extract()
    scan_manifest()


def cmd_self_test(args) -> None:
    """把 w1-checklist 的验收项做成可机器复算的断言（synthetic + 产物一致性）。

    覆盖：抗混叠、滤波器实测响应、端点检测精度、切窗不变量、z-score、划分可复现、
    windows.npy 与 windows.csv 一致。输出 data/reports/w1/self-test.json。
    """
    out: dict = {}
    fails: list[str] = []
    sos8 = design_bandpass()

    # 1) 实测滤波响应（在 8 kHz 上设计与实现）
    w, h = signal.sosfreqz(sos8, worN=65536, fs=FILTER_STAGE_FS)
    mag = 20 * np.log10(np.maximum(np.abs(h), 1e-12))
    def att(f_hz: float) -> float:
        i = int(np.argmin(np.abs(w - f_hz)))
        return round(float(mag[i]), 2)
    # 两个 -3 dB 点：通带内第一个 / 最后一个越过 -3 dB 的频率
    m3 = (w >= 2.0) & (w <= 1500.0)
    w3, a3 = w[m3], mag[m3]
    above = a3 >= -3.0
    hp3 = round(float(w3[int(np.argmax(above))]), 2)
    lp3 = round(float(w3[len(w3) - 1 - int(np.argmax(above[::-1]))]), 2)
    out["filter_response_8k"] = dict(
        design_fs=FILTER_STAGE_FS, sections=int(sos8.shape[0]), poles=int(sos8.shape[0] * 2),
        hp_minus3db_hz=hp3, lp_minus3db_hz=lp3,
        atten_10hz_db=att(10), atten_15hz_db=att(15), atten_100hz_db=att(100),
        atten_500hz_db=att(500), atten_2000hz_db=att(2000), atten_2600hz_db=att(2600),
        atten_3500hz_db=att(3500), max_abs_coeff=float(np.max(np.abs(sos8))),
    )
    hz = out["filter_response_8k"]
    if not (17 <= hz["hp_minus3db_hz"] <= 23):
        fails.append(f"高通 -3 dB 点 {hz['hp_minus3db_hz']} Hz 偏离 20 Hz")
    if not (980 <= hz["lp_minus3db_hz"] <= 1000):
        fails.append(f"低通 -3 dB 点 {hz['lp_minus3db_hz']} Hz 偏离 1000 Hz")

    # 2a) 抗混叠（合成）：2.6 kHz 单音抽到 2 kHz 会折到 600 Hz；测残余功率
    #     注：filtfilt 在纯音两端各有一小段初始化瞬态，故取内部区间作为滤波器本身的抑制量；
    #     真实录音上两端能量仅为信号 RMS 的 ~3%（端点检测已把静音剪掉），见 real_* 两项。
    tone_f, alias_f = 2600.0, 600.0
    tone = 0.1 * np.sin(2 * np.pi * tone_f * np.arange(2 * FS_RAW) / FS_RAW)  # 2 s
    y_tone = resample_to_2k(bandpass_zero_phase(tone, sos8))
    pipe_att_db = 10 * np.log10(max(float(np.mean(y_tone[200:-200] ** 2)), 1e-30)
                                / float(np.mean(tone ** 2)))
    naive = tone[::DECIM]                       # 简单抽取：不抗混叠
    naive_att_db = 10 * np.log10(max(float(np.mean(naive ** 2)), 1e-30)
                                 / float(np.mean(tone ** 2)))

    # 2b) 真实录音上注入同一单音：600 Hz 带内功率不应有可察觉增量
    p = sorted((EXTRACT_DIR / CLASSES[0]).glob("*.wav"))[0]
    x = read_wav_i16(p)
    ep = endpoint_detect(x)
    seg = x[ep["start"]:ep["end"]]
    rms = float(np.sqrt(np.mean(seg ** 2)))
    t = np.arange(seg.size) / FS_RAW
    inj = seg + 0.1 * rms * np.sin(2 * np.pi * tone_f * t)
    y = resample_to_2k(bandpass_zero_phase(inj, sos8))
    y_ref = resample_to_2k(bandpass_zero_phase(seg, sos8))
    f, py = signal.welch(y, fs=FS, nperseg=min(2048, y.size))
    _, pr = signal.welch(y_ref, fs=FS, nperseg=min(2048, y_ref.size))
    band = (f >= alias_f - 8) & (f <= alias_f + 8)
    excess_db = 10 * np.log10(max(np.trapezoid(py[band], f[band]), 1e-30)
                              / max(np.trapezoid(pr[band], f[band]), 1e-30))
    out["aliasing_test"] = dict(
        injected_hz=tone_f, alias_hz=alias_f, injected_amplitude=0.1,
        pipeline_interior_atten_db=round(float(pipe_att_db), 2),
        naive_decimation_atten_db=round(float(naive_att_db), 2),
        real_recording=p.stem,
        real_recording_600hz_excess_db=round(float(excess_db), 3),
        note="pipeline_interior：带通→1/4 抽取 内部区间的残余功率；naive：直接抽取（混叠项几乎不衰减）",
    )
    if pipe_att_db > -40:
        fails.append(f"抗混叠不足：2.6 kHz 单音只衰减 {-pipe_att_db:.1f} dB")
    if excess_db > 1.0:
        fails.append(f"真实录音注入后 600 Hz 带内增量 {excess_db:.2f} dB > 1 dB")

    # 3) 端点检测精度（合成信号：0.3 s 静音 + 1.0 s 音 + 0.4 s 静音）
    rng = np.random.default_rng(0)
    lead, body, tail = int(0.3 * FS_RAW), int(1.0 * FS_RAW), int(0.4 * FS_RAW)
    syn = np.concatenate([
        rng.normal(0, 1e-4, lead),
        np.sin(2 * np.pi * 60 * np.arange(body) / FS_RAW) * 0.3
        + rng.normal(0, 1e-3, body),
        rng.normal(0, 1e-4, tail),
    ])
    e2 = endpoint_detect(syn)
    frame = int(round(EP_FRAME_MS * 1e-3 * FS_RAW))
    start_err = abs(e2["start"] - lead) / FS_RAW * 1000
    end_err = abs(e2["end"] - (lead + body)) / FS_RAW * 1000
    out["endpoint_synthetic"] = dict(
        true_start_s=lead / FS_RAW, true_end_s=(lead + body) / FS_RAW,
        got_start_s=e2["start"] / FS_RAW, got_end_s=e2["end"] / FS_RAW,
        start_err_ms=round(start_err, 2), end_err_ms=round(end_err, 2),
        frame_ms=EP_FRAME_MS, note="单帧内（25 ms）视为通过",
    )
    if max(start_err, end_err) > EP_FRAME_MS:
        fails.append(f"端点检测误差 {max(start_err, end_err):.1f} ms > 一帧")

    # 4) 切窗不变量
    inv_ok = True
    for n in (2130, 4000, 4001, 4570, 6000, 6001, 7970):
        wins = enumerate_windows(n)
        covered = set()
        for s, e, pl, pr in wins:
            if s < 0 or e > n or (e - s) + pl + pr != WIN_N:
                inv_ok = False
            covered.update(range(s, min(e, n)))
        if len(covered) != n:
            inv_ok = False
        if not (1 <= len(wins) <= 3):
            inv_ok = False
    out["window_invariants"] = dict(
        checked_lengths=[2130, 4000, 4001, 4570, 6000, 6001, 7970],
        full_coverage_and_length_ok=bool(inv_ok),
        rule="0 起步进 1 s；尾部未覆盖则补一个尾对齐窗；<2 s 两端补零",
    )
    if not inv_ok:
        fails.append("切窗不变量不成立（覆盖/长度/窗数）")

    # 5) 产物一致性
    W = np.load(OUT_DIR / "windows.npy")
    rows = list(csv.DictReader((OUT_DIR / "windows.csv").open(encoding="utf-8")))
    mu = W.mean(axis=1)
    sd = W.std(axis=1)
    out["windows_tensor"] = dict(
        shape=list(W.shape), dtype=str(W.dtype),
        csv_rows=len(rows), rows_match=bool(W.shape[0] == len(rows)),
        mean_abs_max=round(float(np.max(np.abs(mu))), 6),
        std_min=round(float(sd.min()), 6), std_max=round(float(sd.max()), 6),
        padded_frac=round(float(np.mean([int(r["padded"]) for r in rows])), 4),
        finite=bool(np.all(np.isfinite(W))),
    )
    wt = out["windows_tensor"]
    if not wt["rows_match"] or not wt["finite"]:
        fails.append("windows.npy 与 windows.csv 不一致或含非有限值")
    if wt["mean_abs_max"] > 1e-5 or not (0.999 <= wt["std_min"] and wt["std_max"] <= 1.001):
        fails.append(f"z-score 归一化不成立（|mean|max={wt['mean_abs_max']}, std∈[{wt['std_min']},{wt['std_max']}]）")

    # 6) 逐样本重建抽查：从原始 wav 重算 windows.npy 前 3 行，必须逐点吻合
    manifest = scan_manifest()
    sos_chk = design_bandpass()
    spot = []
    head = rows[:3]
    for ri, meta in enumerate(head):
        rec = next(r for r in manifest if r["recording_id"] == meta["recording_id"])
        x = read_wav_i16(ROOT / rec["path"])
        ep = endpoint_detect(x)
        seg = x[ep["start"]:ep["end"]]
        y2 = resample_to_2k(bandpass_zero_phase(seg, sos_chk))
        s0 = int(meta["start_sample"]); e0 = int(meta["end_sample"])
        pl = int(meta["pad_left"]); pr = int(meta["pad_right"])
        w = zscore(np.pad(y2[s0:e0], (pl, pr), mode="constant"))[0].astype(np.float32)
        spot.append(dict(recording_id=meta["recording_id"], window_idx=int(meta["window_idx"]),
                         max_abs_diff=float(np.max(np.abs(w - W[ri])))))
    out["window_reconstruction"] = dict(
        checked=[d["recording_id"] + f"_w{d['window_idx']}" for d in spot],
        max_abs_diff=round(max(d["max_abs_diff"] for d in spot), 9),
        tolerance=1e-6,
    )
    if max(d["max_abs_diff"] for d in spot) > 1e-6:
        fails.append(f"从原始 wav 重建的窗与 windows.npy 不一致（最大差 {out['window_reconstruction']['max_abs_diff']}）")

    # 7) 划分可复现：用同一 manifest 重建，必须与 splits.json 完全一致
    rebuilt = build_splits(manifest)
    on_disk = json.loads((OUT_DIR / "splits.json").read_text(encoding="utf-8"))
    same = (rebuilt["holdout"] == on_disk["holdout"]
            and rebuilt["folds"] == on_disk["folds"])
    out["splits_deterministic"] = dict(
        identical=bool(same), seed=SPLIT_SEED,
        holdout=len(on_disk["holdout"]), folds=[len(f) for f in on_disk["folds"]],
    )
    if not same:
        fails.append("splits.json 重建结果不一致（不可复现）")

    # 8) 窗只出现在其录音所属的折中
    split_of = {}
    for rid in on_disk["holdout"]:
        split_of[rid] = "holdout"
    for fi, fold in enumerate(on_disk["folds"]):
        for rid in fold:
            split_of[rid] = f"fold{fi}"
    leak = [r for r in rows if r["split"] != split_of[r["recording_id"]]]
    out["no_window_crosses_folds"] = dict(violations=len(leak))
    if leak:
        fails.append(f"{len(leak)} 个窗的 split 与其录音不一致")

    out["result"] = "PASS" if not fails else "FAIL"
    out["failures"] = fails
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    (FIG_DIR / "self-test.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if fails:
        raise SystemExit(f"自检失败：{len(fails)} 项 → {fails}")
    print(f"✓ 自检全部通过（{FIG_DIR / 'self-test.json'}）")


def cmd_verify(args) -> None:
    """重跑全流程，比较数据产物哈希（可复现性验收）。"""
    out = OUT_DIR
    before = {n: sha256_file(out / n) for n in
              ["manifest.csv", "windows.csv", "quality.csv", "splits.json", "windows.npy"]
              if (out / n).exists()}
    if not before:
        raise SystemExit("还没有产物，先跑 all")
    cmd_all(argparse.Namespace(reextract=False, verify_extract=False))
    after = {n: sha256_file(out / n) for n in before}
    diffs = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    if diffs:
        for k, (a, b) in diffs.items():
            print(f"  ✗ {k}: {a[:16]}… → {b[:16]}…")
        raise SystemExit(f"可复现性验收失败：{len(diffs)} 个产物哈希变了")
    print(f"✓ 可复现性验收通过：{len(before)} 个产物逐字节一致（{SPLIT_CREATED}）")


def cmd_probe_filter(args) -> None:
    """ADR-036 证据：20–1000 Hz 带通在 8 kHz 上做 vs 在 2 kHz 上做（上边沿被迫下移）。

    对 N 条录音比较两条链的 PSD 差异与波形相关性，输出写入 data/reports/w1/。
    """
    n_rec = args.n
    sos8 = design_bandpass()
    results = []
    files = sorted((EXTRACT_DIR / CLASSES[0]).glob("*.wav"))[:n_rec]
    for p in files:
        x = read_wav_i16(p)
        ep = endpoint_detect(x)
        seg = x[ep["start"]:ep["end"]]
        a = resample_to_2k(bandpass_zero_phase(seg, sos8))          # A: 8 kHz 带通后抽取
        b0 = resample_to_2k(seg)                                    # B 的基础：先抽取
        f, pa = signal.welch(a, fs=FS, nperseg=min(1024, a.size))
        row = dict(recording_id=p.stem, n=len(a))
        for upper in (990.0, 999.0, 400.0):
            sos2 = signal.butter(FILTER_ORDER, [BAND_HZ[0], upper], btype="bandpass",
                                 fs=FS, output="sos")
            b = signal.sosfiltfilt(sos2, b0, padtype="odd",
                                   padlen=min(3 * (2 * len(sos2) + 1), b0.size - 1))
            _, pb = signal.welch(b, fs=FS, nperseg=min(1024, b.size))
            m = (f >= 20) & (f < 950)
            d = 10 * np.log10(np.maximum(pb[m], 1e-30)) - 10 * np.log10(np.maximum(pa[m], 1e-30))
            row[f"upper_{upper:g}"] = dict(
                max_abs_db=round(float(np.max(np.abs(d))), 3),
                mean_db=round(float(np.mean(d)), 3),
            )
        # 波形一致性（相关性 / 相对 RMS 差）
        for upper in (990.0, 999.0):
            sos2 = signal.butter(FILTER_ORDER, [BAND_HZ[0], upper], btype="bandpass",
                                 fs=FS, output="sos")
            b = signal.sosfiltfilt(sos2, b0, padtype="odd",
                                   padlen=min(3 * (2 * len(sos2) + 1), b0.size - 1))
            L = min(len(a), len(b))
            ea = a[:L] - a[:L].mean()
            eb = b[:L] - b[:L].mean()
            row[f"corr_{upper:g}"] = float(np.corrcoef(ea, eb)[0, 1])
            row[f"reldiff_{upper:g}"] = float(np.sqrt(np.mean((ea - eb) ** 2)) /
                                              max(1e-12, np.sqrt(np.mean(ea ** 2))))
        results.append(row)

    try:
        sos_lit = signal.butter(FILTER_ORDER, [BAND_HZ[0], BAND_HZ[1]], btype="bandpass",
                                fs=FS, output="sos")
        literal_ok = True
        err = ""
    except Exception as exc:  # noqa: BLE001
        literal_ok, err, sos_lit = False, f"{type(exc).__name__}: {exc}", None
    del sos_lit

    agg = {
        "literal_2k_implementable": literal_ok,
        "literal_2k_error": err,
        "n_recordings": len(results),
        "sos_sections_8k": int(sos8.shape[0]),
        "summary": {
            k: {
                "max_abs_db_max": max(r[k]["max_abs_db"] for r in results),
                "mean_abs_db_max": max(abs(r[k]["mean_db"]) for r in results),
            }
            for k in results[0] if isinstance(results[0][k], dict)
        },
        "waveform": {
            u: {
                "corr_min": float(min(r[f"corr_{u}"] for r in results)),
                "reldiff_max": float(max(r[f"reldiff_{u}"] for r in results)),
            }
            for u in ("990", "999")
        },
    }
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    (FIG_DIR / "filter-placement-probe.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(agg, ensure_ascii=False, indent=2))


def cmd_figures(args) -> None:
    """目视验收图（验收项：端点 30 条、重采样频谱 5 条、带通对比 5 条、BER 直方图、随机窗）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    manifest = scan_manifest()
    sos8 = design_bandpass()
    rng = np.random.default_rng(SPLIT_SEED)

    # 1) 端点检测 30 条
    picks = [manifest[i] for i in sorted(rng.choice(len(manifest), 30, replace=False))]
    fig, axes = plt.subplots(6, 5, figsize=(18, 12), sharex=False)
    for ax, rec in zip(axes.ravel(), picks):
        x = read_wav_i16(ROOT / rec["path"])
        ep = endpoint_detect(x)
        t = np.arange(x.size) / FS_RAW
        ax.plot(t, x, lw=0.4, color="#333")
        ax.axvline(ep["start"] / FS_RAW, color="tab:red", lw=1)
        ax.axvline(ep["end"] / FS_RAW, color="tab:red", lw=1)
        ax.set_title(f"{rec['recording_id']} {rec['label']} "
                     f"({(ep['end']-ep['start'])/FS_RAW:.2f}s kept)", fontsize=8)
        ax.set_yticks([])
        ax.tick_params(labelsize=7)
    fig.suptitle("W1 endpoint detection: red = auto start/end (25 ms frame / 10 ms hop, threshold = 3x noise floor; E4)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "01-endpoints-30.png", dpi=120)
    plt.close(fig)

    # 2) 重采样前后频谱 5 条
    pick2 = [manifest[i] for i in sorted(rng.choice(len(manifest), 5, replace=False))]
    fig, axes = plt.subplots(5, 2, figsize=(14, 14))
    for row, rec in zip(axes, pick2):
        x = read_wav_i16(ROOT / rec["path"])
        ep = endpoint_detect(x)
        seg = x[ep["start"]:ep["end"]]
        y8 = bandpass_zero_phase(seg, sos8)
        y2 = resample_to_2k(y8)
        f8, p8 = signal.welch(y8, fs=FS_RAW, nperseg=1024)
        f2, p2 = signal.welch(y2, fs=FS, nperseg=1024)
        row[0].semilogy(f8, p8, lw=0.7)
        row[0].axvline(1000, color="tab:red", ls="--", lw=1)
        row[0].set_title(f"{rec['recording_id']}: 8 kHz (red = 1 kHz, target Nyquist)", fontsize=8)
        row[1].semilogy(f2, p2, lw=0.7, color="tab:green")
        row[1].set_title("2 kHz (after 1/4 decimation)", fontsize=8)
        row[1].set_xlim(0, 1000)
        for ax in row:
            ax.tick_params(labelsize=7)
            ax.set_ylabel("PSD", fontsize=7)
    fig.suptitle("W1 resampling: polyphase FIR (up=1 / down=4) after 20-1000 Hz band-pass at 8 kHz")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "02-resample-spectra-5.png", dpi=120)
    plt.close(fig)

    # 3) 带通 20–1000 Hz 前后波形 5 条
    fig, axes = plt.subplots(5, 2, figsize=(15, 12))
    for row, rec in zip(axes, pick2):
        x = read_wav_i16(ROOT / rec["path"])
        ep = endpoint_detect(x)
        seg = x[ep["start"]:ep["end"]]
        y8 = bandpass_zero_phase(seg, sos8)
        t = np.arange(seg.size) / FS_RAW
        row[0].plot(t, seg, lw=0.4, color="#555")
        row[0].set_title(f"{rec['recording_id']}: before band-pass (8 kHz)", fontsize=8)
        row[1].plot(t, y8, lw=0.4, color="tab:blue")
        row[1].set_title("after 20-1000 Hz band-pass, sosfiltfilt (zero phase)", fontsize=8)
        for ax in row:
            ax.tick_params(labelsize=7)
            ax.set_yticks([])
    fig.suptitle("W1 band-pass 20-1000 Hz, 4th order, sosfiltfilt (zero phase, ADR-034/036)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "03-bandpass-5.png", dpi=120)
    plt.close(fig)

    # 4) BER 直方图 + 分位点
    qual = list(csv.DictReader((OUT_DIR / "quality.csv").open(encoding="utf-8")))
    ber = np.array([float(r["ber"]) for r in qual if r["ber"] not in ("inf", "nan")])
    qs = {q: float(np.quantile(ber, q)) for q in (0.05, 0.10, 0.20)}
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(np.clip(ber, 0, np.quantile(ber, 0.995)), bins=120, color="#888")
    for q, v in qs.items():
        ax.axvline(v, ls="--", lw=1.2, label=f"p{int(q*100)} = {v:.2f}")
    ax.set_yscale("log")
    ax.set_xlabel("BER = E(20–700 Hz) / E(0–20 Hz + 700–1000 Hz)")
    ax.set_ylabel("windows (log)")
    ax.set_title("W1 BER distribution (reject threshold is set per training fold in W3; no rejection in W1)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "04-ber-hist.png", dpi=120)
    plt.close(fig)

    # 5) 随机 5 窗：波形 + 频谱
    W = np.load(OUT_DIR / "windows.npy", mmap_mode="r")
    meta = list(csv.DictReader((OUT_DIR / "windows.csv").open(encoding="utf-8")))
    pickw = sorted(rng.choice(len(meta), 5, replace=False))
    fig, axes = plt.subplots(5, 2, figsize=(14, 14))
    for row, i in zip(axes, pickw):
        w = np.asarray(W[i], dtype=float)
        t = np.arange(w.size) / FS
        row[0].plot(t, w, lw=0.5)
        row[0].set_title(f"{meta[i]['recording_id']} w{meta[i]['window_idx']} "
                         f"{meta[i]['label']} {meta[i]['split']}", fontsize=8)
        row[0].set_ylabel("z-score", fontsize=7)
        f, p = signal.welch(w, fs=FS, nperseg=1024)
        row[1].semilogy(f, p, lw=0.7)
        row[1].set_xlim(0, 1000)
        for ax in row:
            ax.tick_params(labelsize=7)
    fig.suptitle("W1 five random windows after per-window z-score (no clipping, no dead silence)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "05-random-windows-5.png", dpi=120)
    plt.close(fig)

    # 6) 每类窗数
    per_class = {}
    for r in meta:
        per_class[r["label"]] = per_class.get(r["label"], 0) + 1
    fig, ax = plt.subplots(figsize=(7, 4))
    ks = CLASSES
    ax.bar(ks, [per_class.get(k, 0) for k in ks], color="#4c72b0")
    for i, k in enumerate(ks):
        ax.text(i, per_class.get(k, 0), str(per_class.get(k, 0)), ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("windows")
    ax.set_title("W1 windows per class\n(recording-level splits; windows inherit the recording split)",
                 fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "06-windows-per-class.png", dpi=120)
    plt.close(fig)

    print(f"图已写入 {FIG_DIR}")
    for p in sorted(FIG_DIR.glob("*.png")):
        print(f"  {p.name} {p.stat().st_size/1024:.0f} KB")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W1 预处理流水线（参数冻结，出处见 docs/parameter-ledger.md）")
    ap.add_argument("cmd", nargs="?", default="all",
                    choices=["all", "extract", "verify", "figures", "probe-filter", "self-test"])
    ap.add_argument("--reextract", action="store_true", help="强制重新解包")
    ap.add_argument("--verify-extract", action="store_true", help="解包目录存在时也重跑校验")
    ap.add_argument("--n", type=int, default=50, help="probe-filter 的录音条数")
    args = ap.parse_args(argv)
    if args.cmd == "all":
        cmd_all(args)
    elif args.cmd == "extract":
        cmd_extract(args)
    elif args.cmd == "verify":
        cmd_verify(args)
    elif args.cmd == "figures":
        cmd_figures(args)
    elif args.cmd == "probe-filter":
        cmd_probe_filter(args)
    elif args.cmd == "self-test":
        cmd_self_test(args)


if __name__ == "__main__":
    main()
