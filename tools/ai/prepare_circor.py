#!/usr/bin/env python3
"""Prepare the CirCor DigiScope 1.0.3 dataset for patient-level experiments.

This script only uses the Python standard library at this stage. It:

1. extracts the public PhysioNet archive into ``data/raw/circor/extracted``;
2. indexes every WAV recording with its subject-level label;
3. emits a patient-level stratified 70/15/15 split.

The split unit is the subject (patient), never the individual recording. All
recordings of one subject stay in exactly one split.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import wave
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

LABEL_MAP = {"Absent": 0, "Present": 1}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--zip",
        default="data/raw/circor/circor-heart-sound-1.0.3.zip",
        help="Path to the downloaded PhysioNet ZIP archive.",
    )
    parser.add_argument(
        "--extract-dir",
        default="data/raw/circor/extracted",
        help="Directory used for the extracted archive.",
    )
    parser.add_argument(
        "--out-dir",
        default="data/processed/circor",
        help="Directory for subjects.csv, recordings.csv and splits.csv.",
    )
    parser.add_argument("--seed", type=int, default=20261007)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument(
        "--max-subjects-per-class",
        type=int,
        default=0,
        help="Optional smoke-test limit. 0 keeps every labelled subject.",
    )
    return parser.parse_args()


def extract_archive(zip_path: Path, extract_dir: Path) -> None:
    marker = extract_dir / ".extracted"
    if marker.exists():
        print(f"[prepare] archive already extracted: {extract_dir}")
        return
    if not zip_path.exists():
        raise FileNotFoundError(
            f"archive not found: {zip_path}; run tools/ai/download_circor.mjs first"
        )
    extract_dir.mkdir(parents=True, exist_ok=True)
    print(f"[prepare] extracting {zip_path} -> {extract_dir}")
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)
    marker.write_text("ok\n", encoding="utf-8")


def find_training_dir(extract_dir: Path) -> Path:
    candidates = [p for p in extract_dir.rglob("training_data") if p.is_dir()]
    if candidates:
        return candidates[0]
    wavs = list(extract_dir.rglob("*.wav"))
    if not wavs:
        raise FileNotFoundError(f"no WAV files found under {extract_dir}")
    return wavs[0].parent


def read_wav_header(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as handle:
        return handle.getframerate(), handle.getnframes(), handle.getnchannels()


def load_subjects(metadata_csv: Path) -> list[dict]:
    rows: list[dict] = []
    with metadata_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            patient_id = (row.get("Patient ID") or "").strip()
            if not patient_id:
                continue
            murmur = (row.get("Murmur") or "").strip()
            rows.append(
                {
                    "subject_id": patient_id,
                    "murmur": murmur,
                    "label": LABEL_MAP.get(murmur),
                    "outcome": (row.get("Outcome") or "").strip(),
                    "age": (row.get("Age") or "").strip(),
                    "sex": (row.get("Sex") or "").strip(),
                    "campaign": (row.get("Campaign") or "").strip(),
                }
            )
    return rows


def index_recordings(training_dir: Path, subjects: list[dict]) -> tuple[list[dict], list[dict]]:
    wav_by_subject: dict[str, list[Path]] = defaultdict(list)
    for wav_path in sorted(training_dir.glob("*.wav")):
        subject_id = wav_path.stem.split("_")[0]
        wav_by_subject[subject_id].append(wav_path)

    subject_rows: list[dict] = []
    recording_rows: list[dict] = []
    for subject in subjects:
        subject_id = subject["subject_id"]
        wavs = wav_by_subject.get(subject_id, [])
        total_duration = 0.0
        for wav_path in wavs:
            sample_rate, n_frames, channels = read_wav_header(wav_path)
            duration = n_frames / sample_rate if sample_rate else 0.0
            total_duration += duration
            tsv_path = wav_path.with_suffix(".tsv")
            recording_rows.append(
                {
                    **subject,
                    "record_id": wav_path.stem,
                    "location": wav_path.stem.split("_")[-1],
                    "wav_path": str(wav_path),
                    "tsv_path": str(tsv_path) if tsv_path.exists() else "",
                    "sample_rate": sample_rate,
                    "n_samples": n_frames,
                    "channels": channels,
                    "duration_s": round(duration, 3),
                }
            )
        subject_rows.append(
            {
                **subject,
                "n_recordings": len(wavs),
                "total_duration_s": round(total_duration, 3),
            }
        )
    return subject_rows, recording_rows


def stratified_patient_split(
    subject_rows: list[dict],
    seed: int,
    train_ratio: float,
    val_ratio: float,
    max_per_class: int,
) -> dict[str, str]:
    by_label: dict[int, list[dict]] = defaultdict(list)
    for row in subject_rows:
        if row["label"] in (0, 1) and row["n_recordings"] > 0:
            by_label[row["label"]].append(row)

    rng = random.Random(seed)
    split_of: dict[str, str] = {}
    for label in (0, 1):
        rows = sorted(by_label[label], key=lambda item: item["subject_id"])
        rng.shuffle(rows)
        if max_per_class > 0:
            rows = rows[:max_per_class]
        n_total = len(rows)
        n_train = int(round(n_total * train_ratio))
        n_val = int(round(n_total * val_ratio))
        for index, row in enumerate(rows):
            if index < n_train:
                split = "train"
            elif index < n_train + n_val:
                split = "val"
            else:
                split = "test"
            split_of[row["subject_id"]] = split
    return split_of


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    zip_path = Path(args.zip)
    extract_dir = Path(args.extract_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    extract_archive(zip_path, extract_dir)
    training_dir = find_training_dir(extract_dir)
    metadata_csv = find_metadata_csv(extract_dir, training_dir)
    print(f"[prepare] training data: {training_dir}")
    print(f"[prepare] metadata: {metadata_csv}")

    subjects = load_subjects(metadata_csv)
    subject_rows, recording_rows = index_recordings(training_dir, subjects)
    split_of = stratified_patient_split(
        subject_rows,
        seed=args.seed,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        max_per_class=args.max_subjects_per_class,
    )
    for row in subject_rows:
        row["split"] = split_of.get(row["subject_id"], "unused")
    for row in recording_rows:
        row["split"] = split_of.get(row["subject_id"], "unused")

    subject_fields = [
        "subject_id",
        "murmur",
        "label",
        "outcome",
        "age",
        "sex",
        "campaign",
        "n_recordings",
        "total_duration_s",
        "split",
    ]
    recording_fields = [
        "subject_id",
        "label",
        "murmur",
        "outcome",
        "split",
        "record_id",
        "location",
        "wav_path",
        "tsv_path",
        "sample_rate",
        "n_samples",
        "channels",
        "duration_s",
    ]
    write_csv(out_dir / "subjects.csv", subject_rows, subject_fields)
    write_csv(out_dir / "recordings.csv", recording_rows, recording_fields)

    summary: dict = {
        "dataset": "CirCor DigiScope 1.0.3",
        "source": "https://physionet.org/content/circor-heart-sound/1.0.3/",
        "license": "ODC-By 1.0",
        "seed": args.seed,
        "task": "murmur Present(1) vs Absent(0); Unknown excluded",
        "max_subjects_per_class": args.max_subjects_per_class,
        "subjects_total": len(subject_rows),
        "subjects_with_recordings": sum(1 for row in subject_rows if row["n_recordings"] > 0),
        "recordings_total": len(recording_rows),
        "labelled_subjects": len(split_of),
        "split_subjects": dict(Counter(split_of.values())),
        "split_labels": {
            split: dict(
                Counter(
                    row["label"]
                    for row in subject_rows
                    if row["split"] == split and row["label"] in (0, 1)
                )
            )
            for split in ("train", "val", "test")
        },
        "label_counts": dict(Counter(row["murmur"] for row in subject_rows)),
        "sample_rates": dict(Counter(row["sample_rate"] for row in recording_rows)),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"[prepare] wrote {out_dir / 'subjects.csv'}")
    print(f"[prepare] wrote {out_dir / 'recordings.csv'}")
    print(f"[prepare] wrote {out_dir / 'summary.json'}")


def find_metadata_csv(extract_dir: Path, training_dir: Path) -> Path:
    for candidate in (
        training_dir / "training_data.csv",
        training_dir.parent / "training_data.csv",
        extract_dir / "training_data.csv",
    ):
        if candidate.exists():
            return candidate
    matches = list(extract_dir.rglob("training_data.csv"))
    if not matches:
        raise FileNotFoundError("training_data.csv not found in extracted archive")
    return matches[0]


if __name__ == "__main__":
    main()
