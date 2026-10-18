#!/usr/bin/env python3
"""Heart-rate accuracy of the DSP detector against CirCor annotations.

ADR-008's fallback keeps heart rate as a product output when S1/S2 event
labelling is not accepted. That promise needs its own measurement: heart rate
is derived from the same detected events, so a detector that mislabels S1/S2
can still be right about the *period* (which needs no labels) or wrong about
both.

Reference heart rate comes from the annotation events (S1/S2 pairs); detected
heart rate is ``hs_s1s2.estimate_heart_rate`` on the annotation-masked
detections, i.e. exactly what the device would report. Errors are reported as
absolute bpm and stratified by reference rate, because the >120 bpm group is
where the labeller fails.

Usage:
    python tools/ai/evaluate_heart_rate.py --dataset data/processed/circor
"""

from __future__ import annotations

import argparse
import csv
import json
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
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", default="data/reports/heart-rate-circor.json")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def summarize_errors(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0}
    values = np.abs(np.asarray(errors, dtype=float))
    return {
        "n": int(len(values)),
        "mean_abs_bpm": float(np.mean(values)),
        "median_abs_bpm": float(np.median(values)),
        "p95_abs_bpm": float(np.percentile(values, 95)),
        "max_abs_bpm": float(np.max(values)),
        "within_5_bpm": float(np.mean(values <= 5.0)),
        "within_10_bpm": float(np.mean(values <= 10.0)),
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

    records: list[dict] = []
    errors: list[float] = []
    errors_low: list[float] = []
    errors_high: list[float] = []
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

        error = detected_hr - reference_hr
        errors.append(error)
        bucket = errors_high if reference_hr > 120.0 else errors_low
        bucket.append(error)
        records.append(
            {
                "record_id": row["record_id"],
                "subject_id": row["subject_id"],
                "reference_hr_bpm": float(reference_hr),
                "detected_hr_bpm": float(detected_hr),
                "error_bpm": float(error),
            }
        )

    if not records:
        raise SystemExit("no usable recordings")

    payload = {
        "dataset": str(dataset),
        "recordings": len(records),
        "skipped": skipped,
        "reference": "heart rate from CirCor S1/S2 annotation pairs",
        "detected": "hs_s1s2.estimate_heart_rate on annotation-masked detections",
        "all": summarize_errors(errors),
        "reference_hr_le_120": summarize_errors(errors_low),
        "reference_hr_gt_120": summarize_errors(errors_high),
        "per_record": records,
    }
    print(f"recordings: {len(records)} (skipped {skipped})")
    for key in ("all", "reference_hr_le_120", "reference_hr_gt_120"):
        stats = payload[key]
        if not stats.get("n"):
            print(f"{key:<20} no data")
            continue
        print(
            f"{key:<20} n={stats['n']:<5d} "
            f"MAE={stats['mean_abs_bpm']:.2f} bpm  "
            f"median={stats['median_abs_bpm']:.2f}  "
            f"p95={stats['p95_abs_bpm']:.2f}  "
            f"within5={stats['within_5_bpm'] * 100:.1f}%  "
            f"within10={stats['within_10_bpm'] * 100:.1f}%"
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\n[wrote] {out}")


if __name__ == "__main__":
    main()
