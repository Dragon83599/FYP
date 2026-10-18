#!/usr/bin/env python3
"""Freeze a patient-level stratified fold assignment into ``splits.csv``.

Reads ``subjects.csv`` and ``recordings.csv`` from ``--data-dir`` (the manifest
shape produced by ``prepare_circor.py`` and ``synthetic_pcg.py``), assigns every
labelled patient to one fold of a stratified group K-fold, and writes:

- ``<out>`` (default ``<data-dir>/splits.csv``): the frozen protocol, in the
  format documented in ``hs_splits.py``;
- ``<out stem>-summary.json`` (default ``<data-dir>/splits-summary.json``): the
  per-fold patient counts, per-fold per-label counts, fingerprint, seed,
  ``n_folds``, the seed with its ``seed_protocol`` label (``2026-10-08 /
  ADR-007`` for the default seed) and the source manifest paths, so a report can
  cite the split without re-deriving it.

The clinically important unit is the patient: a subject with several recordings
always lands in exactly one fold, and the script refuses to run if the manifest
disagrees with itself (a subject whose recordings carry different labels, or a
label that differs between ``subjects.csv`` and ``recordings.csv``).

Only recordings whose ``split`` column is ``train``/``val``/``test`` and whose
label is ``0``/``1`` define the cross-validation cohort, matching
``cross_validate.load_rows``; labelled patients without such a recording, and
patients with only ``Unknown`` recordings, are reported and excluded.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from hs_splits import (
    DEFAULT_N_FOLDS,
    DEFAULT_SEED,
    fold_label_counts,
    load_splits,
    patient_folds,
    seed_protocol,
    splits_fingerprint,
    validate_splits,
    write_splits,
)

#: ``split`` values that take part in cross-validation (see cross_validate.py).
SPLIT_VALUES = ("train", "val", "test")

#: ``label`` values that are usable as a binary CV target.
LABEL_VALUES = ("0", "1")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--data-dir",
        default="data/processed/circor",
        help="Directory containing subjects.csv and recordings.csv.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output CSV path (default: <data-dir>/splits.csv).",
    )
    parser.add_argument("--n-folds", type=int, default=DEFAULT_N_FOLDS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args()


def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"{path}: not found; run prepare_circor.py or synthetic_pcg.py first")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def dataset_name(data_dir: Path) -> str:
    """Prefer the dataset string recorded by the preparation step."""
    summary_path = data_dir / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        name = str(summary.get("dataset") or "").strip()
        if name:
            return name
    return data_dir.name


def load_cohort(data_dir: Path) -> tuple[dict[str, int], list[dict], list[str]]:
    """Return ``(subject labels, eligible recordings, warnings)``.

    Raises ``SystemExit`` with an actionable message when the manifest is
    internally inconsistent, because a split frozen from an inconsistent
    manifest would silently leak the wrong label into training or evaluation.
    """
    subjects_path = data_dir / "subjects.csv"
    recordings_path = data_dir / "recordings.csv"
    subject_rows = read_manifest(subjects_path)
    recording_rows = read_manifest(recordings_path)
    warnings: list[str] = []

    declared: dict[str, int] = {}
    for row in subject_rows:
        subject = (row.get("subject_id") or "").strip()
        if not subject:
            continue
        value = (row.get("label") or "").strip()
        if value not in LABEL_VALUES:
            continue  # Unknown / missing label: not a binary CV patient.
        label = int(value)
        if subject in declared and declared[subject] != label:
            raise SystemExit(
                f"{subjects_path}: subject {subject!r} has conflicting labels "
                f"{declared[subject]} and {label}"
            )
        declared[subject] = label
    if not declared:
        raise SystemExit(
            f"{subjects_path}: no patient with label 0/1; nothing to freeze"
        )

    labels_from_recordings: dict[str, set[int]] = defaultdict(set)
    eligible: list[dict] = []
    for row in recording_rows:
        subject = (row.get("subject_id") or "").strip()
        if not subject:
            continue
        value = (row.get("label") or "").strip()
        if value not in LABEL_VALUES:
            continue
        label = int(value)
        labels_from_recordings[subject].add(label)
        split = (row.get("split") or "").strip()
        if split in SPLIT_VALUES:
            eligible.append(row)

    inconsistent = {
        subject: sorted(values)
        for subject, values in labels_from_recordings.items()
        if len(values) > 1
    }
    if inconsistent:
        detail = "; ".join(
            f"{subject}: labels {values}"
            for subject, values in sorted(inconsistent.items())[:5]
        )
        raise SystemExit(
            f"{recordings_path}: {len(inconsistent)} subject(s) have inconsistent "
            f"labels across their recordings ({detail}); refusing to freeze a split"
        )

    for subject, values in labels_from_recordings.items():
        label = sorted(values)[0]
        if subject in declared and declared[subject] != label:
            raise SystemExit(
                f"{recordings_path}: subject {subject!r} has label {label} but "
                f"subjects.csv says {declared[subject]}; refusing to freeze a split"
            )
        declared.setdefault(subject, label)

    eligible_subjects = sorted({(row.get("subject_id") or "").strip() for row in eligible})
    if not eligible_subjects:
        raise SystemExit(
            f"{recordings_path}: no labelled recording with split in "
            f"{SPLIT_VALUES}; nothing to freeze"
        )
    missing_declared = [subject for subject in eligible_subjects if subject not in declared]
    if missing_declared:
        warnings.append(
            f"{len(missing_declared)} subject(s) are absent from subjects.csv and "
            f"take their label from recordings.csv (first: {missing_declared[:5]})"
        )
    excluded = sorted(set(declared) - set(eligible_subjects))
    if excluded:
        warnings.append(
            f"{len(excluded)} labelled subject(s) have no labelled recording in "
            f"{SPLIT_VALUES} and are excluded (first: {excluded[:5]})"
        )

    cohort = {subject: declared[subject] for subject in eligible_subjects}
    label_counts = Counter(cohort.values())
    if len(label_counts) < 2:
        raise SystemExit(
            f"{subjects_path}: the cohort has a single label class "
            f"({dict(label_counts)}); a stratified CV protocol needs both"
        )
    return cohort, eligible, warnings


def main() -> None:
    args = parse_args()
    if args.n_folds < 2:
        raise SystemExit(f"--n-folds must be >= 2, got {args.n_folds}")
    data_dir = Path(args.data_dir)
    out_path = Path(args.out) if args.out else data_dir / "splits.csv"
    summary_path = out_path.parent / f"{out_path.stem}-summary.json"

    cohort, eligible, warnings = load_cohort(data_dir)
    for message in warnings:
        print(f"[freeze] warning: {message}")
    label_counts = Counter(cohort.values())
    print(
        f"[freeze] cohort: {len(cohort)} labelled patients "
        f"(label 0: {label_counts.get(0, 0)}, label 1: {label_counts.get(1, 0)}), "
        f"{len(eligible)} recordings"
    )

    protocol = seed_protocol(args.seed)
    print(
        f"[freeze] protocol: n_folds={args.n_folds} seed={args.seed} "
        f"({protocol})"
    )
    folds = patient_folds(cohort, n_folds=args.n_folds, seed=args.seed)
    validate_splits(folds, cohort, args.n_folds, strict_extra=True)
    counts = fold_label_counts(folds, cohort, args.n_folds)
    fold_sizes = {fold: sum(per_label.values()) for fold, per_label in counts.items()}
    degenerate = [
        f"fold {fold} has no patient with label {label}"
        for fold in counts
        for label in sorted(label_counts)
        if counts[fold].get(label, 0) == 0
    ]
    if degenerate:
        raise SystemExit(
            f"cannot freeze {args.n_folds} folds: " + "; ".join(degenerate[:5])
            + f" (label counts {dict(label_counts)}; reduce --n-folds)"
        )

    fingerprint = splits_fingerprint(folds)
    maximum_deviation = max(
        abs(counts[fold].get(label, 0) - label_counts[label] / args.n_folds)
        for fold in counts
        for label in sorted(label_counts)
    )
    metadata = {
        "dataset": dataset_name(data_dir),
        "n_folds": args.n_folds,
        "seed": args.seed,
        "seed_protocol": protocol,
        "source_manifest": str(data_dir / "subjects.csv"),
        "recordings_manifest": str(data_dir / "recordings.csv"),
        "fingerprint": fingerprint,
        "generator": "tools/ai/freeze_splits.py",
    }
    write_splits(out_path, folds, cohort, metadata)

    # Verify the artifact instead of trusting the write path.
    reloaded = load_splits(out_path)
    if reloaded != folds:
        raise SystemExit(f"{out_path}: written file does not round-trip to the assignment")
    if splits_fingerprint(reloaded) != fingerprint:
        raise SystemExit(f"{out_path}: written file has an unexpected fingerprint")

    summary = {
        "dataset": metadata["dataset"],
        "splits_file": str(out_path),
        "summary_file": str(summary_path),
        "source_manifest": str(data_dir / "subjects.csv"),
        "recordings_manifest": str(data_dir / "recordings.csv"),
        "n_folds": args.n_folds,
        "seed": args.seed,
        "seed_protocol": protocol,
        "total_patients": len(cohort),
        "total_recordings": len(eligible),
        "label_counts": {str(label): label_counts[label] for label in sorted(label_counts)},
        "fold_patient_counts": {
            str(fold): fold_sizes[fold] for fold in sorted(fold_sizes)
        },
        "fold_label_counts": {
            str(fold): {
                str(label): counts[fold].get(label, 0) for label in sorted(label_counts)
            }
            for fold in sorted(counts)
        },
        "per_class_max_abs_deviation_from_ideal": round(maximum_deviation, 6),
        "fingerprint": fingerprint,
        "fingerprint_algorithm": "sha256",
        "fingerprint_input": "sorted '<subject_id>\\t<fold>\\n' over the patient->fold map",
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"[freeze] fingerprint: {fingerprint}")
    print(f"[freeze] wrote {out_path}")
    print(f"[freeze] wrote {summary_path}")


if __name__ == "__main__":
    main()
