"""CirCor segmentation annotation helpers.

Each ``*.tsv`` file contains ``start_seconds  end_seconds  label`` rows where
label 1 is S1, 2 is systole, 3 is S2, 4 is diastole and 0 is unannotated or
low-quality signal. CirCor only annotates the high-quality representative
sections, so these labels can be used to select more reliable training windows.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def load_segments(path: str | Path) -> np.ndarray:
    if not path:
        return np.zeros((0, 3), dtype=np.float64)
    tsv_path = Path(path)
    if not tsv_path.exists():
        return np.zeros((0, 3), dtype=np.float64)
    return parse_segments(tsv_path.read_text(encoding="utf-8"))


def parse_segments(text: str) -> np.ndarray:
    rows: list[tuple[float, float, int]] = []
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        try:
            rows.append((float(parts[0]), float(parts[1]), int(float(parts[2]))))
        except ValueError:
            continue
    return np.asarray(rows, dtype=np.float64) if rows else np.zeros((0, 3), dtype=np.float64)


def annotation_labels(
    n_samples: int, segments: np.ndarray, sample_rate: int
) -> np.ndarray:
    labels = np.zeros(n_samples, dtype=np.int8)
    for start_seconds, end_seconds, label in segments:
        start = max(0, int(round(start_seconds * sample_rate)))
        end = min(n_samples, int(round(end_seconds * sample_rate)))
        if end > start:
            labels[start:end] = int(label)
    return labels


def annotated_fraction(labels: np.ndarray, start: int, length: int) -> float:
    if length <= 0:
        return 0.0
    end = min(len(labels), start + length)
    start = max(0, start)
    if end <= start:
        return 0.0
    return float(np.count_nonzero(labels[start:end])) / float(end - start)


def annotated_window_starts(
    n_samples: int,
    segments: np.ndarray,
    sample_rate: int,
    window_samples: int,
    hop_samples: int,
    min_annotated_fraction: float,
) -> np.ndarray:
    if window_samples <= 0 or hop_samples <= 0 or n_samples < window_samples:
        return np.zeros(0, dtype=np.int64)
    labels = annotation_labels(n_samples, segments, sample_rate)
    starts = []
    for start in range(0, n_samples - window_samples + 1, hop_samples):
        if annotated_fraction(labels, start, window_samples) >= min_annotated_fraction:
            starts.append(start)
    return np.asarray(starts, dtype=np.int64)
