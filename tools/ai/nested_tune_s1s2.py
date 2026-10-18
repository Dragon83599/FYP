#!/usr/bin/env python3
"""Nested patient-level selection of the S1/S2 labeller hyper-parameters.

Motivation (the methodological debt recorded in the handover): the four
parameters ``same_state_penalty``, ``min_gap_separation``, ``merge_fraction``
and ``merge_min_gap_s`` were chosen while looking at the same cohort that later
produced the reported macro F1. That makes 0.772 potentially optimistic.

This script removes the leak:

1. Outer loop over the frozen patient-level folds. For each outer fold k, the
   recordings of the other folds are the *tuning* set and fold k is *held out*.
2. The parameter grid is scored only on the tuning set (patient-level macro F1
   on the annotation-masked event metric).
3. The single best configuration is evaluated once on the held-out fold.
4. The reported number is the mean/std of the held-out scores across folds.
   The current defaults are evaluated on the same held-out folds for a fair
   comparison, so the optimism of the old number is directly measurable.

Nothing about the held-out fold influences selection: the cache, the sampling
seed and the grid are all derived from the tuning folds only.

Usage:
    python tools/ai/nested_tune_s1s2.py --dataset data/processed/circor
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hs_events import add_counts, counts, evaluate, segments_to_events, summarize
from hs_features import read_wav_mono
from hs_s1s2 import DetectorConfig, detect_s1s2
from hs_segments import annotated_intervals, load_segments, mask_times

#: The configuration that produced the previously reported 0.772.
DEFAULT_PARAMS = {
    "same_state_penalty": 1.5,
    "min_gap_separation": 1.05,
    "merge_fraction": 0.55,
    "merge_min_gap_s": 0.12,
}

TOLERANCE_S = 0.1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--splits", default="", help="default <dataset>/splits.csv")
    parser.add_argument("--tuning-recordings", type=int, default=500,
                        help="recordings sampled from the tuning folds (memory/speed bound)")
    parser.add_argument("--folds", default="0,1,2,3,4")
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument("--out", default="data/reports/s1s2-nested-tuning.json")
    return parser.parse_args()


def load_folds(path: Path) -> dict[str, int]:
    folds: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("subject_id"):
            continue
        parts = line.split(",")
        if len(parts) >= 2:
            folds[parts[0]] = int(parts[1])
    return folds


def build_grid() -> list[dict]:
    """Coarse grid centred on the previous defaults, 27 configurations.

    merge_min_gap_s only matters when merging is enabled, so it is not crossed
    with merge_fraction=0 (that would duplicate identical configurations).
    """
    grid: list[dict] = []
    for penalty in (0.5, 1.5, 3.0):
        for separation in (1.02, 1.05, 1.10):
            grid.append(
                {
                    "same_state_penalty": penalty,
                    "min_gap_separation": separation,
                    "merge_fraction": 0.0,
                    "merge_min_gap_s": 0.12,
                }
            )
            for merge_gap in (0.10, 0.15):
                grid.append(
                    {
                        "same_state_penalty": penalty,
                        "min_gap_separation": separation,
                        "merge_fraction": 0.55,
                        "merge_min_gap_s": merge_gap,
                    }
                )
    return grid


def dataset_rows(dataset: Path, folds: dict[str, int]) -> list[dict]:
    rows = []
    with (dataset / "recordings.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            tsv = (row.get("tsv_path") or "").strip()
            if not tsv or row.get("subject_id") not in folds:
                continue
            rows.append(row)
    return rows


def load_cache(rows: list[dict]) -> list[tuple]:
    """Load and keep signals for the tuning set only (bounded by --tuning-recordings)."""
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
                signal.astype(np.float32),
                sample_rate,
                intervals,
            )
        )
    return cache


def _summarize(per_patient: dict[str, dict]) -> dict:
    rows = [{**values, "patient_id": patient} for patient, values in per_patient.items()]
    summary = summarize(rows)
    return {
        "patients": summary["patients"],
        "macro_f1": summary["macro"]["f1_macro"]["mean"],
        "macro_f1_std": summary["macro"]["f1_macro"]["std"],
        "swap_rate": summary["pooled"]["swap_rate"],
        "n_reference": summary["pooled"]["n_reference"],
    }


def score_cache(cache, parameters: dict) -> dict:
    per_patient: dict[str, dict] = {}
    for patient, reference, signal, sample_rate, intervals in cache:
        config = DetectorConfig(sample_rate=sample_rate, **parameters)
        detected = detect_s1s2(signal, config).events
        keep = mask_times(detected.times, intervals)
        detected = detected.__class__(
            times=detected.times[keep], labels=detected.labels[keep]
        )
        row = counts(evaluate(reference, detected, TOLERANCE_S))
        per_patient[patient] = add_counts(per_patient.get(patient, {}), row)
    return _summarize(per_patient)


def score_streaming(rows: list[dict], parameters: dict) -> dict:
    """Score a held-out set without keeping every signal in memory."""
    per_patient: dict[str, dict] = {}
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
        reference = segments_to_events(segments)
        config = DetectorConfig(sample_rate=sample_rate, **parameters)
        detected = detect_s1s2(signal, config).events
        keep = mask_times(detected.times, intervals)
        detected = detected.__class__(
            times=detected.times[keep], labels=detected.labels[keep]
        )
        row_counts = counts(evaluate(reference, detected, TOLERANCE_S))
        patient = row["subject_id"]
        per_patient[patient] = add_counts(per_patient.get(patient, {}), row_counts)
    return _summarize(per_patient)


def selection_key(result: dict) -> tuple:
    """Rank by macro F1, break ties by the lower swap rate."""
    return (result["macro_f1"], -result["swap_rate"])


def main() -> None:
    args = parse_args()
    dataset = Path(args.dataset)
    splits = Path(args.splits) if args.splits else dataset / "splits.csv"
    folds = load_folds(splits)
    rows = dataset_rows(dataset, folds)
    outer_folds = [int(value) for value in args.folds.split(",") if value.strip()]
    grid = build_grid()

    print(f"recordings: {len(rows)}  patients: {len(folds)}  outer folds: {outer_folds}")
    print(f"grid: {len(grid)} configurations; tuning sample: {args.tuning_recordings}")

    per_fold: list[dict] = []
    default_heldout: list[dict] = []
    selected_heldout: list[dict] = []
    selection_counts: dict[str, int] = {}

    for fold in outer_folds:
        tuning_rows = [row for row in rows if folds[row["subject_id"]] != fold]
        held_rows = [row for row in rows if folds[row["subject_id"]] == fold]
        rng = np.random.default_rng(args.seed + fold)
        if args.tuning_recordings and len(tuning_rows) > args.tuning_recordings:
            index = rng.choice(len(tuning_rows), args.tuning_recordings, replace=False)
            tuning_rows = [tuning_rows[i] for i in index]

        cache = load_cache(tuning_rows)
        scored = []
        for parameters in grid:
            metrics = score_cache(cache, parameters)
            scored.append({"parameters": parameters, "tuning": metrics})
        del cache

        best = max(scored, key=lambda item: selection_key(item["tuning"]))
        key = json.dumps(best["parameters"], sort_keys=True)
        selection_counts[key] = selection_counts.get(key, 0) + 1

        held_selected = score_streaming(held_rows, best["parameters"])
        held_default = score_streaming(held_rows, DEFAULT_PARAMS)
        selected_heldout.append(held_selected)
        default_heldout.append(held_default)

        per_fold.append(
            {
                "fold": fold,
                "tuning_recordings": len(tuning_rows),
                "heldout_recordings": len(held_rows),
                "selected": best["parameters"],
                "tuning": best["tuning"],
                "heldout_selected": held_selected,
                "heldout_default": held_default,
                "top5": sorted(
                    (
                        {"parameters": item["parameters"], "tuning": item["tuning"]}
                        for item in scored
                    ),
                    key=lambda item: selection_key(item["tuning"]),
                    reverse=True,
                )[:5],
            }
        )
        print(
            f"[fold {fold}] tuning={len(tuning_rows)} held={len(held_rows)} "
            f"selected={best['parameters']} "
            f"tuningF1={best['tuning']['macro_f1']:.3f} "
            f"heldF1(selected)={held_selected['macro_f1']:.3f} "
            f"heldF1(default)={held_default['macro_f1']:.3f}"
        )

    def mean_std(results: list[dict], key: str) -> dict:
        values = np.asarray([item[key] for item in results], dtype=float)
        return {
            "mean": float(np.mean(values)),
            "std": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
            "values": [float(value) for value in values],
        }

    nested = {
        "macro_f1_selected": mean_std(selected_heldout, "macro_f1"),
        "swap_selected": mean_std(selected_heldout, "swap_rate"),
        "macro_f1_default": mean_std(default_heldout, "macro_f1"),
        "swap_default": mean_std(default_heldout, "swap_rate"),
    }

    print("\n=== nested estimate (held-out folds only) ===")
    print(
        f"nested selection : macro F1 {nested['macro_f1_selected']['mean']:.3f}"
        f" ± {nested['macro_f1_selected']['std']:.3f}  "
        f"swap {nested['swap_selected']['mean']:.3f}"
    )
    print(
        f"fixed defaults   : macro F1 {nested['macro_f1_default']['mean']:.3f}"
        f" ± {nested['macro_f1_default']['std']:.3f}  "
        f"swap {nested['swap_default']['mean']:.3f}"
    )
    print(
        "selected configurations across folds: "
        + ", ".join(f"{count}×{key}" for key, count in sorted(selection_counts.items()))
    )
    verdict = (
        "PASS (>= 0.80)"
        if nested["macro_f1_selected"]["mean"] >= 0.80
        else "FAIL (ADR-008 requires event F1 >= 0.80)"
    )
    print(f"ADR-008 verdict: {verdict}")

    payload = {
        "protocol": {
            "outer_folds": outer_folds,
            "tuning_recordings_per_fold": args.tuning_recordings,
            "grid_size": len(grid),
            "selection_metric": "patient-level macro F1 on tuning folds, tie-break lower swap",
            "tolerance_ms": TOLERANCE_S * 1000,
            "annotation_mask": True,
            "default_params": DEFAULT_PARAMS,
        },
        "nested": nested,
        "verdict": verdict,
        "selected_config_counts": selection_counts,
        "per_fold": per_fold,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\n[wrote] {out}")


if __name__ == "__main__":
    main()
