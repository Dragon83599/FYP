#!/usr/bin/env python3
"""Parameter sweep for the DSP S1/S2 detector.

Reproduces the sweep that selected the default band and envelope width: build
the dataset with ``synthetic_pcg.py``, then run this script. The grid is
deliberately coarse; the goal is to find a stable plateau, not a single tuned
point. Any parameter chosen here is still only E4 evidence and must be
re-validated on CirCor.

Usage:
    python tools/ai/sweep_s1s2.py --dataset data/processed/synthetic-pcg
"""

from __future__ import annotations

import argparse
import csv
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hs_events import add_counts, counts, evaluate, segments_to_events, summarize
from hs_features import read_wav_mono
from hs_s1s2 import DetectorConfig, detect_s1s2
from hs_segments import load_segments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--band-high", default="70,80,90,100")
    parser.add_argument("--envelope-ms", default="30,35,40")
    parser.add_argument("--prominence", default="0.60")
    parser.add_argument("--tolerance-ms", type=float, default=100.0)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def floats(text: str) -> list[float]:
    return [float(value) for value in text.split(",") if value.strip()]


def load_cache(dataset: Path, limit: int):
    with (dataset / "recordings.csv").open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle)]
    if limit > 0:
        rows = rows[:limit]
    cache = []
    for row in rows:
        tsv = (row.get("tsv_path") or "").strip()
        path = Path(tsv) if tsv else Path(row["wav_path"]).with_suffix(".tsv")
        if not path.exists():
            continue
        reference = segments_to_events(load_segments(path))
        if len(reference) == 0:
            continue
        try:
            signal, sample_rate = read_wav_mono(row["wav_path"])
        except (OSError, ValueError):
            continue
        cache.append((row.get("subject_id", "unknown"), signal, sample_rate, reference))
    return cache


def run(cache, tolerance_s: float, **parameters) -> dict:
    per_patient: dict[str, dict] = {}
    for patient, signal, sample_rate, reference in cache:
        result = detect_s1s2(
            signal, DetectorConfig(sample_rate=sample_rate, **parameters)
        )
        report = evaluate(reference, result.events, tolerance_s)
        row = counts(report)
        per_patient[patient] = add_counts(per_patient.get(patient, {}), row)
    rows = [{**values, "patient_id": patient} for patient, values in per_patient.items()]
    summary = summarize(rows)
    return {
        "f1_macro": summary["macro"]["f1_macro"],
        "f1_s1": summary["macro"]["f1_s1"],
        "f1_s2": summary["macro"]["f1_s2"],
        "swap_rate": summary["pooled"]["swap_rate"],
        "n_detected": summary["pooled"]["n_detected"],
        "n_reference": summary["pooled"]["n_reference"],
    }


def main() -> None:
    args = parse_args()
    cache = load_cache(Path(args.dataset), args.limit)
    if not cache:
        raise SystemExit("no annotated recordings found")
    print(f"recordings: {len(cache)}")
    print(
        f"{'band_high':>9} {'env_ms':>7} {'prom':>5} "
        f"{'F1_macro':>9} {'swap':>6} {'det/ref':>10}"
    )
    for band_high, envelope_ms, prominence in itertools.product(
        floats(args.band_high), floats(args.envelope_ms), floats(args.prominence)
    ):
        metrics = run(
            cache,
            args.tolerance_ms / 1000.0,
            band_high_hz=band_high,
            envelope_ms=envelope_ms,
            prominence_fraction=prominence,
        )
        print(
            f"{band_high:9.1f} {envelope_ms:7.1f} {prominence:5.2f} "
            f"{metrics['f1_macro']['mean']:9.3f} "
            f"{metrics['swap_rate']:6.3f} "
            f"{metrics['n_detected']:5d}/{metrics['n_reference']:<5d}"
        )


if __name__ == "__main__":
    main()
