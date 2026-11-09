#!/usr/bin/env python3
"""Build the unlabelled corpus from PhysioNet/CinC 2016, as the paper does.

Ma 2023 §2.1 says the unlabelled pool is the interpretable PCG from four
databases in the 2016 challenge collection — Aalborg, Haute-Alsace, Dalian and
Shiraz — **with the normal/abnormal labels discarded**. Their scale is about
25,000 two-second clips.

CinC 2016 ships the databases as separate folders. Aalborg is training-a and
training-b, Haute-Alsace is training-c, Dalian is training-d, Shiraz is
training-e; training-f belongs to none of the four and is left out. The labels
in REFERENCE.csv are read only to count the corpus, never used for training.

Recordings are 2 kHz and about 36 s long, so each yields ~17 non-overlapping 2 s
clips at 1 kHz. That is more than the paper used, so the corpus is subsampled to
--limit with a fixed seed.

    python tools/ai/make_ssl_corpus.py --limit 25000
    python tools/ai/make_ssl_corpus.py --check
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np

FOLDERS = ("training-a", "training-b", "training-c", "training-d", "training-e")
DEFAULT_ROOT = Path("/mnt/e/cinc2016")
DEFAULT_OUT = Path("/mnt/e/cinc2016/unlabelled_1k.npy")


def load_recordings(root: Path) -> list[Path]:
    files: list[Path] = []
    for folder in FOLDERS:
        files.extend(sorted((root / folder).glob("*.wav")))
    if not files:
        raise SystemExit(f"no wav files under {root}/{FOLDERS}")
    return files


def windows_from_recording(x: np.ndarray, fs: int, rate: int = 1000, seconds: int = 2) -> np.ndarray:
    """Decimate to `rate` and cut non-overlapping windows, min-max each."""
    from scipy.signal import resample_poly

    if fs != rate:
        x = resample_poly(x, 1, fs // rate)
    length = rate * seconds
    count = x.shape[0] // length
    if count == 0:
        return np.empty((0, length), dtype=np.float32)
    blocks = x[: count * length].reshape(count, length).astype(np.float32)
    low = blocks.min(axis=1, keepdims=True)
    high = blocks.max(axis=1, keepdims=True)
    span = np.where(high - low < 1e-9, 1.0, high - low)
    return ((blocks - low) / span * 2.0 - 1.0).astype(np.float32)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=25000)
    parser.add_argument("--seed", type=int, default=20261109)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    from scipy.io import wavfile

    files = load_recordings(args.root)
    print(f"recordings: {len(files):,}")

    chunks = []
    total = 0
    for i, path in enumerate(files):
        fs, x = wavfile.read(path)
        scale = 32768.0 if x.dtype == np.int16 else 1.0
        if x.ndim > 1:
            x = x[:, 0]
        blocks = windows_from_recording(x.astype(np.float32) / scale, fs)
        if blocks.size:
            chunks.append(blocks)
            total += blocks.shape[0]
        if (i + 1) % 400 == 0:
            print(f"  {i + 1:,}/{len(files):,} recordings, {total:,} windows", flush=True)

    X = np.concatenate(chunks, axis=0)
    print(f"windows before subsampling: {X.shape}")

    if args.limit and X.shape[0] > args.limit:
        rng = np.random.default_rng(args.seed)
        keep = rng.choice(X.shape[0], size=args.limit, replace=False)
        keep.sort()
        X = X[keep]
    print(f"windows kept: {X.shape}")

    if args.check:
        print("range", float(X.min()), float(X.max()), "std", round(float(X.std()), 4))
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.out, X)
    summary = {
        "source": "PhysioNet/CinC Challenge 2016 training a-e (labels discarded)",
        "folders": list(FOLDERS),
        "recordings": len(files),
        "windows": int(X.shape[0]),
        "window_samples": int(X.shape[1]),
        "rate_hz": 1000,
        "limit": args.limit,
        "seed": args.seed,
        "path": str(args.out),
    }
    (args.out.parent / "ssl-corpus-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("saved ->", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
