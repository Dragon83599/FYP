#!/usr/bin/env python3
"""Derive an alternative label manifest from the prepared CirCor manifest.

The prepared dataset carries more than the murmur label: ``Outcome`` is
Normal/Abnormal (486/456 patients, almost balanced) and the ``Campaign`` field
(CC2014 385 patients, CC2015 557) can be turned into a two-fold split that
measures domain shift instead of random-fold generalisation.

This script writes a *new* data directory so the original prepared manifest is
never modified, and every downstream script (cross_validate, train_mfcc,
train_cnn) works unchanged because the schema stays the same:

    <out>/subjects.csv  recordings.csv  splits.csv  summary.json

Usage:
    # second binary target: abnormal vs normal, 942 patients, balanced
    python tools/ai/make_label_manifest.py --source data/processed/circor \
        --out data/processed/circor-outcome --label outcome

    # domain shift: train on one campaign, test on the other
    python tools/ai/make_label_manifest.py --source data/processed/circor \
        --out data/processed/circor-campaign --label murmur --splits campaign
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hs_splits import DEFAULT_SEED, patient_folds, splits_fingerprint, write_splits

LABEL_MAPS = {
    "murmur": {"Absent": 0, "Present": 1},
    "outcome": {"Normal": 0, "Abnormal": 1},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="prepared manifest directory")
    parser.add_argument("--out", required=True, help="output directory (created)")
    parser.add_argument("--label", choices=sorted(LABEL_MAPS), default="murmur")
    parser.add_argument("--splits", choices=("frozen", "campaign"), default="frozen")
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args()


def read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    source = Path(args.source)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    subjects, subject_fields = read_csv(source / "subjects.csv")
    recordings, recording_fields = read_csv(source / "recordings.csv")

    mapping = LABEL_MAPS[args.label]
    label_field = "murmur" if args.label == "murmur" else "outcome"

    kept_subjects: list[dict] = []
    labels: dict[str, int] = {}
    for row in subjects:
        raw = (row.get(label_field) or "").strip()
        if raw not in mapping:
            continue
        label = mapping[raw]
        labels[row["subject_id"]] = label
        kept_subjects.append({**row, "label": str(label)})

    if not kept_subjects:
        raise SystemExit(f"no subject has a usable '{label_field}' label")

    kept_ids = set(labels)
    kept_recordings = [
        {**row, "label": str(labels[row["subject_id"]])}
        for row in recordings
        if row["subject_id"] in kept_ids
    ]

    # Every kept subject must have at least one recording, otherwise the split
    # file would reference a patient that can never produce a window.
    have_recordings = {row["subject_id"] for row in kept_recordings}
    missing = sorted(kept_ids - have_recordings)
    if missing:
        raise SystemExit(f"{len(missing)} labelled subject(s) have no recording: {missing[:5]}")

    if args.splits == "frozen":
        folds = patient_folds(labels, args.n_folds, args.seed)
        split_meta = {
            "kind": "frozen",
            "seed": int(args.seed),
            "n_folds": int(args.n_folds),
            "rule": "patient-level stratified, same rule as cross_validate.stratified_folds",
        }
    else:
        campaigns = {}
        for row in subjects:
            if row["subject_id"] in kept_ids:
                campaigns[row["subject_id"]] = (row.get("campaign") or "").strip()
        if set(campaigns.values()) - {"CC2014", "CC2015"}:
            unknown = sorted(set(campaigns.values()) - {"CC2014", "CC2015"})
            raise SystemExit(f"unexpected campaign value(s): {unknown}")
        folds = {subject: (0 if campaign == "CC2014" else 1) for subject, campaign in campaigns.items()}
        split_meta = {
            "kind": "campaign",
            "rule": "fold 0 = CC2014, fold 1 = CC2015 (domain shift, not random folds)",
            "n_folds": 2,
        }

    write_csv(out / "subjects.csv", kept_subjects, subject_fields)
    write_csv(out / "recordings.csv", kept_recordings, recording_fields)
    write_splits(out / "splits.csv", folds, labels, {"source": str(source), **split_meta})

    summary = {
        "source": str(source),
        "out": str(out),
        "label": args.label,
        "label_field": label_field,
        "label_map": mapping,
        "subjects": len(kept_subjects),
        "recordings": len(kept_recordings),
        "label_counts": {
            "0": sum(1 for value in labels.values() if value == 0),
            "1": sum(1 for value in labels.values() if value == 1),
        },
        "splits": split_meta,
        "fingerprint": splits_fingerprint(folds),
        "fold_subjects": {
            str(fold): sum(1 for value in folds.values() if value == fold)
            for fold in sorted(set(folds.values()))
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"[manifest] {args.label} / {split_meta['kind']}: "
          f"{len(kept_subjects)} subjects, {len(kept_recordings)} recordings")
    print(f"[manifest] labels: {summary['label_counts']}")
    print(f"[manifest] folds: {summary['fold_subjects']}")
    print(f"[manifest] fingerprint: {summary['fingerprint']}")
    print(f"[manifest] wrote {out}")


if __name__ == "__main__":
    main()
