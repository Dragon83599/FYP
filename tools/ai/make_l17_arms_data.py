#!/usr/bin/env python3
"""Build the datasets the W3 improvements start from: L17, not our own pipeline.

Why this file exists
--------------------
ADR-047 froze the improvement baseline as **L17**: 1 kHz, 2 s (2,000 points),
min-max to [-1, 1], no band-pass, 20,193 parameters, one clip per recording.
The first attempt at W3 used the project's own 2 kHz / 1,791-window pipeline
instead, which is a *different task* -- more windows, overlapping crops, z-score
instead of min-max -- so its 96.66% could not be compared with the 98.40% anchor
and every "improvement" was measured from the wrong place.

This script materialises L17's clips once, plus the variant each improvement
needs, so an arm only has to name the dataset it uses:

    l17_1k        L17 itself: 1 kHz, min-max, no band-pass
    l17_2k        I1: same clips at 2 kHz (kernel doubled to keep 20 ms)
    l17_bp400_1k  I12a: 20-400 Hz zero-phase band-pass, then 1 kHz
    l17_bp150_1k  I12b: 20-150 Hz zero-phase band-pass, then 1 kHz

Every variant is derived from the **same 1,000 clips in the same order**, so the
folds and the paired comparison stay valid across arms. The band-pass is applied
on the 8 kHz signal before decimation, exactly as ADR-036 requires for our own
pipeline -- filtering after decimation would fold the stop-band back in.

    .venv/bin/python tools/ai/make_l17_arms_data.py
    .venv/bin/python tools/ai/make_l17_arms_data.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repro_ma2023 import CLASSES, FS_TARGET, N_SAMPLES, load_yaseen, read_wav_mono, to_1khz  # noqa: E402

RAW = Path("data/raw/yaseen/extracted")
OUT = Path("data/processed/l17")


def bandpass_8k(x: np.ndarray, low_hz: float, high_hz: float, fs: int = 8000) -> np.ndarray:
    """Zero-phase Butterworth band-pass, 4th order, applied before decimation."""
    from scipy.signal import butter, sosfiltfilt

    sos = butter(4, [low_hz, high_hz], btype="bandpass", fs=fs, output="sos")
    return sosfiltfilt(sos, x)


def clips_with_band(raw_dir: Path, band: tuple[float, float] | None, rate: int) -> np.ndarray:
    """Rebuild the clips the same way `load_yaseen` does, with an optional band-pass."""
    from repro_ma2023 import crop_or_pad, min_max_scale

    out = []
    for label in CLASSES:
        for path in sorted((raw_dir / label).glob("*.wav")):
            x, fs = read_wav_mono(path)
            if band is not None:
                x = bandpass_8k(x, band[0], band[1], fs)
            x = to_1khz(x, fs) if rate == 1000 else to_2khz(x, fs)
            x, _, _ = crop_or_pad(x, n_samples=rate * 2)
            out.append(min_max_scale(x).astype(np.float32))
    return np.stack(out)


def to_2khz(x: np.ndarray, fs: int) -> np.ndarray:
    from scipy.signal import resample_poly

    if fs == 2000:
        return x
    return resample_poly(x, 1, fs // 2000)


def random_start_clips(raw_dir: Path, rate: int, seed: int) -> np.ndarray:
    """L17's clips, but each one cut at a random offset inside its recording.

    This is the honest version of "the probe was placed at an arbitrary moment":
    the window is continuous, taken from somewhere in the recording, instead of
    being a circular shift of an already-cut clip. A circular shift splices the
    two ends together and puts a step discontinuity in the middle of the window,
    which is not something a real recording ever contains.
    """
    from repro_ma2023 import crop_or_pad, min_max_scale

    rng = np.random.default_rng(seed)
    out = []
    for label in CLASSES:
        for path in sorted((raw_dir / label).glob("*.wav")):
            x, fs = read_wav_mono(path)
            x = to_1khz(x, fs) if rate == 1000 else to_2khz(x, fs)
            span = max(0, x.shape[0] - rate * 2)
            start = int(rng.integers(0, span + 1)) if span > 0 else 0
            piece = x[start:start + rate * 2]
            piece, _, _ = crop_or_pad(piece, n_samples=rate * 2)
            out.append(min_max_scale(piece).astype(np.float32))
    return np.stack(out)


def swap_clips(raw_dir: Path, rate: int, seed: int) -> np.ndarray:
    """The paper's perturbation (section 2.5): cut at an arbitrary point, swap halves.

    Applied to the recording *before* pre-processing, exactly as the paper does,
    so the resulting 2 s clip starts at a random phase of the cardiac cycle. The
    same perturbed dataset is used for both training and testing, which is what
    section 3.2 describes.
    """
    from repro_ma2023 import crop_or_pad, min_max_scale

    rng = np.random.default_rng(seed)
    out = []
    for label in CLASSES:
        for path in sorted((raw_dir / label).glob("*.wav")):
            x, fs = read_wav_mono(path)
            cut = int(rng.integers(0, x.shape[0])) if x.shape[0] > 1 else 0
            x = np.concatenate([x[cut:], x[:cut]])
            x = to_1khz(x, fs) if rate == 1000 else to_2khz(x, fs)
            piece, _, _ = crop_or_pad(x, n_samples=rate * 2)
            out.append(min_max_scale(piece).astype(np.float32))
    return np.stack(out)


def build(raw_dir: Path) -> dict[str, np.ndarray]:
    X_1k, y, clips = load_yaseen(raw_dir)          # L17 exactly: 1 kHz, min-max, no band-pass
    X_1k = X_1k[..., 0]
    datasets = {
        "l17_1k": X_1k,
        "l17_2k": clips_with_band(raw_dir, None, 2000),
        "l17_bp400_1k": clips_with_band(raw_dir, (20.0, 400.0), 1000),
        "l17_bp150_1k": clips_with_band(raw_dir, (20.0, 150.0), 1000),
        "l17_1k_randstart": random_start_clips(raw_dir, 1000, seed=1234),
        "l17_1k_swap": swap_clips(raw_dir, 1000, seed=20261109),
        "l17_1k_swap2": swap_clips(raw_dir, 1000, seed=20261110),
        "l17_1k_swap3": swap_clips(raw_dir, 1000, seed=20261111),
    }
    return datasets, y, clips


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", type=Path, default=RAW)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--check", action="store_true", help="verify the files match a rebuild")
    args = parser.parse_args(argv)

    datasets, y, clips = build(args.raw_dir)
    args.out.mkdir(parents=True, exist_ok=True)

    summary = {"n_clips": int(y.size), "classes": list(CLASSES), "datasets": {}}
    for name, X in datasets.items():
        if X.shape[0] != y.size:
            raise SystemExit(f"{name}: {X.shape[0]} clips, expected {y.size}")
        path = args.out / f"{name}.npy"
        if args.check:
            if not path.exists():
                raise SystemExit(f"{path} missing; run without --check")
            if not np.array_equal(np.load(path), X):
                raise SystemExit(f"{path} does not match a rebuild")
        else:
            np.save(path, X)
        summary["datasets"][name] = {
            "shape": list(X.shape),
            "min": round(float(X.min()), 4),
            "max": round(float(X.max()), 4),
            "sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest()[:16],
        }
        print(f"{name:14s} {X.shape}  [{X.min():.3f}, {X.max():.3f}]")

    y_path = args.out / "labels.npy"
    if not args.check:
        np.save(y_path, y)
    summary["labels"] = str(y_path)
    summary["clip_order"] = [c.recording_id for c in clips[:5]] + ["..."]
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("summary ->", args.out / "summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
