#!/usr/bin/env python3
"""Emit the C-vs-Python heart-rate comparison fixture for test_hr_detector.c.

The C port of the heart-rate detector (shared/src/hr_detector.c) is validated
against the Python reference on real CirCor recordings. This script produces the
fixture the C test consumes:

    wav_path,python_hr_bpm,reference_hr_bpm

one row per usable recording, where

* the manifest and the skip rules are exactly those of
  tools/ai/evaluate_heart_rate.py (data/processed/circor/recordings.csv, rows
  with a tsv_path, readable WAV, non-empty annotation, finite reference HR and
  finite detected HR);
* ``python_hr_bpm`` is ``hs_s1s2.estimate_heart_rate`` on the detections that
  fall inside the annotated intervals -- the same annotation-masked value that
  evaluate_heart_rate.py reports as ``detected_hr_bpm``, i.e. what the device
  would show;
* ``reference_hr_bpm`` is ``hs_s1s2.estimate_heart_rate`` on the CirCor S1/S2
  annotation pairs (``hs_events.segments_to_events``);
* ``wav_path`` is the manifest path, so the C test reads the same audio. The C
  test derives the annotation file from it (same basename, ``.tsv``) and applies
  the same interval mask.

Usage:
    python tools/ai/dump_hr_fixture.py --dataset data/processed/circor \
        --out data/reports/hr-fixture-circor.csv

Nothing is downloaded; the WAV/TSV files are read from the local dataset.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hs_events import segments_to_events
from hs_features import read_wav_mono
from hs_s1s2 import DetectorConfig, detect_s1s2, estimate_heart_rate
from hs_segments import annotated_intervals, load_segments, mask_times


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="data/processed/circor")
    parser.add_argument(
        "--out", default="data/reports/hr-fixture-circor.csv", help="fixture CSV"
    )
    parser.add_argument("--limit", type=int, default=0, help="stop after N rows")
    parser.add_argument(
        "--debug-events",
        default="",
        help="optional second CSV with the masked Python event times, for "
        "diagnosing a specific mismatch",
    )
    return parser.parse_args()


def summarize(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0}
    values = np.abs(np.asarray(errors, dtype=float))
    return {
        "n": int(len(values)),
        "mae": float(np.mean(values)),
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "within_2": float(np.mean(values <= 2.0)),
        "within_5": float(np.mean(values <= 5.0)),
    }


def main() -> None:
    args = parse_args()
    dataset = Path(args.dataset)
    rows = [
        row
        for row in csv.DictReader((dataset / "recordings.csv").open(encoding="utf-8"))
        if (row.get("tsv_path") or "").strip()
    ]
    if args.limit:
        rows = rows[: args.limit]

    fixture: list[tuple[str, float, float]] = []
    debug_rows: list[tuple[str, str]] = []
    skipped = 0

    for row in rows:
        segments = load_segments(row["tsv_path"])
        if len(segments) == 0:
            skipped += 1
            continue
        try:
            signal, sample_rate = read_wav_mono(row["wav_path"])
        except (OSError, ValueError):
            skipped += 1
            continue
        intervals = annotated_intervals(segments, len(signal) / sample_rate)
        reference = segments_to_events(segments)
        reference_hr = estimate_heart_rate(reference.times)
        if not np.isfinite(reference_hr):
            skipped += 1
            continue

        detected = detect_s1s2(signal, DetectorConfig(sample_rate=sample_rate)).events
        if intervals:
            keep = mask_times(detected.times, intervals)
            detected = detected.__class__(
                times=detected.times[keep], labels=detected.labels[keep]
            )
        detected_hr = estimate_heart_rate(detected.times)
        if not np.isfinite(detected_hr):
            skipped += 1
            continue

        fixture.append((row["wav_path"], float(detected_hr), float(reference_hr)))
        if args.debug_events:
            debug_rows.append(
                (
                    row["wav_path"],
                    " ".join(f"{t:.6f}" for t in detected.times),
                )
            )

    if not fixture:
        raise SystemExit("no usable recordings")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["wav_path", "python_hr_bpm", "reference_hr_bpm"])
        for wav_path, python_hr, reference_hr in fixture:
            writer.writerow([wav_path, f"{python_hr:.6f}", f"{reference_hr:.6f}"])

    if args.debug_events:
        debug = Path(args.debug_events)
        debug.parent.mkdir(parents=True, exist_ok=True)
        with debug.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["wav_path", "masked_event_times_s"])
            writer.writerows(debug_rows)
        print(f"[wrote] {debug}")

    errors = [python_hr - reference_hr for _, python_hr, reference_hr in fixture]
    stats = summarize(errors)
    print(f"recordings: {len(fixture)} (skipped {skipped})")
    print(
        f"python vs reference  n={stats['n']:<5d} "
        f"MAE={stats['mae']:.2f} bpm  median={stats['median']:.2f}  "
        f"p95={stats['p95']:.2f}  within5={stats['within_5'] * 100:.1f}%"
    )
    print(
        "reference numbers in data/reports/heart-rate-circor.json (3152 "
        "recordings): MAE 6.79, median 1.19, p95 44.2"
    )
    print(f"\n[wrote] {out}")


if __name__ == "__main__":
    main()
