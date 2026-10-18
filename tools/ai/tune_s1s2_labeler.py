#!/usr/bin/env python3
"""Select the S1/S2 labeller evidence weights without touching the test folds.

The detector reads two weak cues: S1 is usually lower-frequency than S2, and
often louder at the auscultation site. Their weights are not guessed on the
full cohort -- that would tune on the evaluation set. Instead the frozen
patient-level folds are split into a tuning part (folds 0..k-1) and a held-out
part (the rest), the weights are chosen on the tuning part, and only then is
the chosen configuration scored on the held-out part.

Usage:
    python tools/ai/tune_s1s2_labeler.py --dataset data/processed/circor \
        --max-tuning-recordings 200
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hs_events import (
    add_counts,
    counts,
    evaluate,
    segments_to_events,
    summarize,
)
from hs_features import read_wav_mono
from hs_s1s2 import DetectorConfig, detect_s1s2
from hs_segments import annotated_intervals, load_segments, mask_times


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--splits", default="", help="default <dataset>/splits.csv")
    parser.add_argument("--tuning-folds", default="0,1,2")
    parser.add_argument("--max-tuning-recordings", type=int, default=200)
    parser.add_argument("--max-heldout-recordings", type=int, default=0)
    parser.add_argument("--zcr-weights", default="0,0.5,1.5")
    parser.add_argument("--amplitude-weights", default="0,0.3")
    parser.add_argument("--out", default="data/reports/s1s2-labeler-tuning.json")
    parser.add_argument("--seed", type=int, default=20261018)
    return parser.parse_args()


def floats(text: str) -> list[float]:
    return [float(value) for value in text.split(",") if value.strip()]


def load_folds(path: Path) -> dict[str, int]:
    folds: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("subject_id"):
            continue
        parts = line.split(",")
        folds[parts[0]] = int(parts[1])
    return folds


def build_cache(dataset: Path, folds: dict[str, int], wanted_folds: set[int], limit: int, seed: int):
    rows = [row for row in csv.DictReader((dataset / "recordings.csv").open(encoding="utf-8"))]
    rows = [
        row
        for row in rows
        if row.get("tsv_path")
        and row.get("subject_id") in folds
        and folds[row["subject_id"]] in wanted_folds
        and row.get("label") in ("0", "1")
    ]
    if limit and len(rows) > limit:
        rng = np.random.default_rng(seed)
        rows = [rows[i] for i in rng.choice(len(rows), limit, replace=False)]
    cache = []
    for row in rows:
        segments = load_segments(row["tsv_path"])
        if len(segments) == 0:
            continue
        try:
            signal, sample_rate = read_wav_mono(row["wav_path"])
        except (OSError, ValueError):
            continue
        intervals = annotated_intervals(segments, len(signal) / sample_rate)
        if not intervals:
            continue
        cache.append(
            (
                row["subject_id"],
                segments_to_events(segments),
                signal,
                sample_rate,
                intervals,
            )
        )
    return cache


def score(cache, **weights) -> dict:
    per_patient: dict[str, dict] = {}
    for patient, reference, signal, sample_rate, intervals in cache:
        config = DetectorConfig(sample_rate=sample_rate, **weights)
        detected = detect_s1s2(signal, config).events
        keep = mask_times(detected.times, intervals)
        detected = detected.__class__(
            times=detected.times[keep], labels=detected.labels[keep]
        )
        report = evaluate(reference, detected, 0.1)
        row = counts(report)
        per_patient[patient] = add_counts(per_patient.get(patient, {}), row)
    rows = [{**values, "patient_id": p} for p, values in per_patient.items()]
    summary = summarize(rows)
    return {
        "patients": summary["patients"],
        "macro_f1": summary["macro"]["f1_macro"]["mean"],
        "macro_f1_std": summary["macro"]["f1_macro"]["std"],
        "swap_rate": summary["pooled"]["swap_rate"],
        "pooled_f1": summary["pooled"]["per_class"],
    }


def main() -> None:
    args = parse_args()
    dataset = Path(args.dataset)
    splits = Path(args.splits) if args.splits else dataset / "splits.csv"
    folds = load_folds(splits)
    tuning_folds = {int(v) for v in args.tuning_folds.split(",") if v.strip()}
    heldout_folds = set(folds.values()) - tuning_folds

    tuning = build_cache(dataset, folds, tuning_folds, args.max_tuning_recordings, args.seed)
    heldout = build_cache(
        dataset, folds, heldout_folds, args.max_heldout_recordings, args.seed + 1
    )
    print(f"tuning recordings: {len(tuning)} (folds {sorted(tuning_folds)})")
    print(f"held-out recordings: {len(heldout)} (folds {sorted(heldout_folds)})")

    grid = [
        {"evidence_weight_zcr": z, "evidence_weight_amplitude": a}
        for z, a in itertools.product(
            floats(args.zcr_weights), floats(args.amplitude_weights)
        )
    ]
    print(f"\n{'zcr_w':>6} {'amp_w':>6} {'tuning macroF1':>16} {'swap':>7}")
    results = []
    for weights in grid:
        metrics = score(tuning, **weights)
        results.append({"weights": weights, "tuning": metrics})
        print(
            f"{weights['evidence_weight_zcr']:6.2f} "
            f"{weights['evidence_weight_amplitude']:6.2f} "
            f"{metrics['macro_f1']:12.3f}±{metrics['macro_f1_std']:.3f} "
            f"{metrics['swap_rate']:7.3f}"
        )

    best = max(results, key=lambda item: item["tuning"]["macro_f1"])
    print(f"\nselected on tuning folds: {best['weights']}")

    if heldout:
        heldout_metrics = score(heldout, **best["weights"])
        baseline_metrics = score(heldout, evidence_weight_zcr=0.0, evidence_weight_amplitude=0.0)
        print("\nHELD-OUT evaluation (never used for selection):")
        for name, metrics in (("no evidence", baseline_metrics), ("selected", heldout_metrics)):
            print(
                f"  {name:12s} patients={metrics['patients']:4d} "
                f"macroF1={metrics['macro_f1']:.3f}±{metrics['macro_f1_std']:.3f} "
                f"swap={metrics['swap_rate']:.3f}"
            )
        payload = {
            "grid": results,
            "selected": best["weights"],
            "heldout": {"baseline": baseline_metrics, "selected": heldout_metrics},
            "tuning_folds": sorted(tuning_folds),
            "heldout_folds": sorted(heldout_folds),
        }
    else:
        payload = {"grid": results, "selected": best["weights"]}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\n[wrote] {out}")


if __name__ == "__main__":
    main()
