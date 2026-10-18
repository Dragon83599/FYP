#!/usr/bin/env python3
"""Evaluate the DSP S1/S2 detector against CirCor-style annotations.

Works on the synthetic smoke-test dataset (``tools/ai/synthetic_pcg.py``) and on
the real CirCor manifest produced by ``tools/ai/prepare_circor.py``. Both write
``recordings.csv`` with ``wav_path`` and ``tsv_path`` columns.

Reports per patient and pooled event-level precision, recall, F1, S1/S2 swap
rate and boundary error, using the +/-100 ms tolerance required by ADR-008.
Metrics are also split by ``labeling_reliable``: near 130-140 bpm the systolic
and diastolic intervals converge and the S1/S2 *order* cannot be resolved from
a single auscultation site, so the unreliable subset must be quoted separately
rather than averaged away.

Results from the synthetic dataset are pipeline validation only (E4). They must
never be reported as detector performance on real heart sounds.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hs_events import (
    add_counts,
    counts,
    describe_errors,
    evaluate,
    segments_to_events,
    summarize,
)
from hs_features import read_wav_mono
from hs_s1s2 import DetectorConfig, detect_s1s2
from hs_segments import annotated_intervals, load_segments, mask_times


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Directory with recordings.csv")
    parser.add_argument("--out", default="", help="JSON report path")
    parser.add_argument("--tolerance-ms", type=float, default=100.0)
    parser.add_argument("--threshold-ratio", type=float, default=2.5)
    parser.add_argument("--refractory-ms", type=float, default=100.0)
    parser.add_argument("--band-high", type=float, default=85.0)
    parser.add_argument("--envelope-ms", type=float, default=38.0)
    parser.add_argument("--prominence", type=float, default=0.60)
    parser.add_argument(
        "--annotation-mask",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Restrict detections to CirCor label 1-4 intervals (default). "
        "Label 0 means unannotated/low-quality, where detections have no "
        "reference and would be scored as false positives.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Evaluate at most N recordings")
    parser.add_argument("--worst", type=int, default=5, help="Print the N worst recordings")
    return parser.parse_args()


def recording_rows(dataset: Path, limit: int) -> list[dict]:
    manifest = dataset / "recordings.csv"
    if not manifest.exists():
        raise SystemExit(f"missing manifest: {manifest}")
    with manifest.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle)]
    if limit > 0:
        rows = rows[:limit]
    return rows


def annotation_path(row: dict) -> Path | None:
    tsv = (row.get("tsv_path") or "").strip()
    if tsv:
        return Path(tsv)
    candidate = Path(row["wav_path"]).with_suffix(".tsv")
    return candidate if candidate.exists() else None


def summarize_counts(mapping: dict[str, dict]) -> dict:
    rows = [{**values, "patient_id": patient} for patient, values in mapping.items()]
    return summarize(rows)


def print_subset(label: str, summary: dict) -> None:
    if summary["patients"] == 0:
        print(f"{label:<26} no data")
        return
    macro = summary["macro"]
    pooled = summary["pooled"]
    print(
        f"{label:<26} patients={summary['patients']:<3d} "
        f"F1(S1)={macro['f1_s1']['mean']:.3f} "
        f"F1(S2)={macro['f1_s2']['mean']:.3f} "
        f"macro={macro['f1_macro']['mean']:.3f}±{macro['f1_macro']['std']:.3f} "
        f"swap={pooled['swap_rate']:.3f} "
        f"ref/det={pooled['n_reference']}/{pooled['n_detected']}"
    )


def main() -> None:
    args = parse_args()
    dataset = Path(args.dataset)
    base_config = DetectorConfig(
        threshold_ratio=args.threshold_ratio,
        refractory_ms=args.refractory_ms,
        band_high_hz=args.band_high,
        envelope_ms=args.envelope_ms,
        prominence_fraction=args.prominence,
    )
    tolerance_s = args.tolerance_ms / 1000.0

    rows = recording_rows(dataset, args.limit)
    per_record: list[dict] = []
    per_patient: dict[str, dict] = {}
    per_patient_reliable: dict[str, dict] = {}
    per_patient_unreliable: dict[str, dict] = {}
    patient_boundary: dict[str, list[float]] = {}
    skipped = 0
    coverage: list[float] = []
    masked_events = 0

    for row in rows:
        tsv_path = annotation_path(row)
        if tsv_path is None or not tsv_path.exists():
            skipped += 1
            continue
        segments = load_segments(tsv_path)
        reference = segments_to_events(segments)
        if len(reference) == 0:
            skipped += 1
            continue
        try:
            signal, sample_rate = read_wav_mono(row["wav_path"])
        except (OSError, ValueError) as error:
            print(f"[warn] {row.get('record_id', '?')}: {error}", file=sys.stderr)
            skipped += 1
            continue

        result = detect_s1s2(signal, base_config.with_sample_rate(sample_rate))
        detected = result.events
        if args.annotation_mask:
            duration_s = len(signal) / float(sample_rate)
            intervals = annotated_intervals(segments, duration_s)
            if intervals:
                keep = mask_times(detected.times, intervals)
                masked_events += int(len(detected) - np.count_nonzero(keep))
                detected = detected.__class__(
                    times=detected.times[keep], labels=detected.labels[keep]
                )
                coverage.append(
                    sum(end - start for start, end in intervals) / duration_s
                )
        report = evaluate(reference, detected, tolerance_s)
        record_counts = counts(report)
        patient = row.get("subject_id") or row.get("record_id") or "unknown"
        record_counts["patient_id"] = patient
        reliable = bool(result.info.get("labeling_reliable", True))
        per_record.append(
            {
                "record_id": row.get("record_id", ""),
                "patient_id": patient,
                "reference_events": int(len(reference)),
                "detected_events": int(len(detected)),
                "heart_rate_bpm": float(result.info.get("heart_rate_bpm", float("nan"))),
                "labeling_reliable": reliable,
                "label_interval_spread": float(
                    result.info.get("label_interval_spread", float("nan"))
                ),
                "counts": {
                    key: value for key, value in record_counts.items() if key != "patient_id"
                },
                "f1_macro": report["f1_macro"],
                "swap_rate": report["matches"]["swap_rate"],
                "boundary_p95_s": report["boundary_error_s"]["p95"],
            }
        )
        per_patient[patient] = add_counts(per_patient.get(patient, {}), record_counts)
        bucket = per_patient_reliable if reliable else per_patient_unreliable
        bucket[patient] = add_counts(bucket.get(patient, {}), record_counts)
        patient_boundary.setdefault(patient, []).extend(report["boundary_errors"])

    if not per_record:
        raise SystemExit("no usable recordings with annotations found")

    overall = summarize_counts(per_patient)
    boundary = describe_errors(
        [value for key in per_patient for value in patient_boundary[key]]
    )
    reliable_summary = summarize_counts(per_patient_reliable)
    unreliable_summary = summarize_counts(per_patient_unreliable)

    payload = {
        "dataset": str(dataset),
        "recordings_evaluated": len(per_record),
        "recordings_skipped": skipped,
        "config": {
            "band_low_hz": base_config.band_low_hz,
            "band_high_hz": base_config.band_high_hz,
            "envelope_ms": base_config.envelope_ms,
            "prominence_fraction": base_config.prominence_fraction,
            "threshold_ratio": base_config.threshold_ratio,
            "refractory_ms": base_config.refractory_ms,
            "tolerance_ms": args.tolerance_ms,
            "annotation_mask": bool(args.annotation_mask),
        },
        "summary": overall,
        "summary_labeling_reliable": reliable_summary,
        "summary_labeling_unreliable": unreliable_summary,
        "annotation_coverage": {
            "median": float(np.median(coverage)) if coverage else float("nan"),
            "mean": float(np.mean(coverage)) if coverage else float("nan"),
            "n": len(coverage),
            "masked_detections": masked_events,
        },
        "boundary_error_s": boundary,
        "per_record": per_record,
    }

    print(f"dataset: {dataset}")
    print(
        f"recordings: {len(per_record)} evaluated, {skipped} skipped "
        f"(no annotation or unreadable)"
    )
    print()
    print_subset("all", overall)
    print_subset("labeling reliable", reliable_summary)
    print_subset("labeling unreliable", unreliable_summary)
    if coverage:
        print(
            f"annotation coverage: median {np.median(coverage):.2f} "
            f"(mean {np.mean(coverage):.2f}); {masked_events} detections "
            f"outside annotated regions dropped"
        )
    if boundary:
        print(
            "\nboundary error (s): "
            f"median={boundary['median']:.4f} p95={boundary['p95']:.4f} "
            f"max={boundary['max']:.4f} n={boundary['n']}"
        )

    if args.worst > 0 and per_record:
        ranked = sorted(per_record, key=lambda item: item["f1_macro"])
        print("\nworst recordings:")
        for item in ranked[: args.worst]:
            print(
                f"  {item['record_id']:<24} macro={item['f1_macro']:.3f} "
                f"swap={item['swap_rate']:.3f} ref={item['reference_events']} "
                f"det={item['detected_events']} hr={item['heart_rate_bpm']:.0f} "
                f"{'reliable' if item['labeling_reliable'] else 'UNRELIABLE'}"
            )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\n[wrote] {out_path}")


if __name__ == "__main__":
    main()
