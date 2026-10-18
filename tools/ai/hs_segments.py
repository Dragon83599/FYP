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


def annotated_intervals(
    segments,
    duration_s: float | None = None,
    labels: tuple[int, ...] = (1, 2, 3, 4),
) -> list[tuple[float, float]]:
    """Merge the time intervals covered by real annotations.

    CirCor writes ``label 0`` rows for unannotated or low-quality signal, and
    those rows can span the whole recording. Any event detected there has no
    reference and would otherwise be counted as a false positive, so event
    metrics must be restricted to the ``labels`` intervals (1=S1, 2=systole,
    3=S2, 4=diastole by default).

    Returns sorted, non-overlapping ``(start, end)`` pairs in seconds.
    """
    segments = np.asarray(segments, dtype=np.float64)
    if segments.size == 0:
        return []
    keep = np.isin(segments[:, 2].astype(int), np.asarray(labels, dtype=int))
    rows = segments[keep]
    if len(rows) == 0:
        return []
    valid = rows[:, 1] > rows[:, 0]
    rows = rows[valid]
    if len(rows) == 0:
        return []
    starts = np.maximum(rows[:, 0], 0.0)
    ends = rows[:, 1] if duration_s is None else np.minimum(rows[:, 1], float(duration_s))
    order = np.argsort(starts, kind="mergesort")
    starts = starts[order]
    ends = ends[order]
    merged: list[list[float]] = []
    for start, end in zip(starts, ends):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], float(end))
        else:
            merged.append([float(start), float(end)])
    return [(start, end) for start, end in merged]


def mask_times(times, intervals: list[tuple[float, float]]) -> np.ndarray:
    """Boolean mask of event times that fall inside any annotated interval."""
    times = np.asarray(times, dtype=np.float64).reshape(-1)
    mask = np.zeros(len(times), dtype=bool)
    for start, end in intervals:
        mask |= (times >= start) & (times <= end)
    return mask
