#!/usr/bin/env python3
"""Train and evaluate the first NumPy heart-murmur baseline.

Baseline A (this script): 20-1000 Hz band-pass -> 2 s windows -> 40-band
log-mel summary features -> L2 logistic regression. Labels are subject-level
``Murmur = Present/Absent``; ``Unknown`` subjects are excluded by
``prepare_circor.py``.

The output is intentionally a first, cheap reference point. It should not be
quoted as the final model for the host MCU.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from hs_features import design_bandpass_fir, filtfilt, iter_windows, read_wav_mono, window_feature_vector
from hs_metrics import best_threshold, binary_metrics, bootstrap_ci
from hs_models import LogisticRegression, StandardScaler
from hs_segments import annotated_window_starts, load_segments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/processed/circor")
    parser.add_argument("--cache", default="data/processed/circor/features-logmel-logreg.npz")
    parser.add_argument("--out-dir", default="data/reports/circor-logreg")
    parser.add_argument("--window-sec", type=float, default=2.0)
    parser.add_argument("--hop-sec", type=float, default=2.0)
    parser.add_argument("--max-windows-per-record", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=600)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--l2", type=float, default=1e-3)
    parser.add_argument("--silence-rms", type=float, default=1e-4)
    parser.add_argument(
        "--window-selection",
        choices=("all", "annotated"),
        default="all",
        help="Use every window, or only windows covered by CirCor segmentation labels.",
    )
    parser.add_argument("--min-annotated-fraction", type=float, default=0.5)
    parser.add_argument("--rebuild-cache", action="store_true")
    return parser.parse_args()


def load_recordings(data_dir: Path) -> list[dict]:
    with (data_dir / "recordings.csv").open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle)]
    return [row for row in rows if row["split"] in ("train", "val", "test") and row["label"] in ("0", "1")]


def build_features(rows: list[dict], args: argparse.Namespace) -> dict[str, np.ndarray]:
    started = time.time()
    features: list[np.ndarray] = []
    labels: list[int] = []
    subjects: list[str] = []
    records: list[str] = []
    skipped_silent = 0
    skipped_unannotated = 0

    for index, row in enumerate(rows, start=1):
        signal, sample_rate = read_wav_mono(row["wav_path"])
        taps = design_bandpass_fir(sample_rate)
        signal = filtfilt(signal, taps)
        window_samples = int(round(args.window_sec * sample_rate))
        hop_samples = int(round(args.hop_sec * sample_rate))
        count_for_record = 0
        allowed_starts = None
        if args.window_selection == "annotated":
            segments = load_segments(row.get("tsv_path", ""))
            allowed_starts = set(
                annotated_window_starts(
                    len(signal),
                    segments,
                    sample_rate,
                    window_samples,
                    hop_samples,
                    args.min_annotated_fraction,
                ).tolist()
            )

        for start, window in iter_windows(signal, window_samples, hop_samples):
            if allowed_starts is not None and start not in allowed_starts:
                skipped_unannotated += 1
                continue
            rms = float(np.sqrt(np.mean(window**2)))
            if rms < args.silence_rms:
                skipped_silent += 1
                continue
            features.append(window_feature_vector(window, sample_rate))
            labels.append(int(row["label"]))
            subjects.append(row["subject_id"])
            records.append(row["record_id"])
            count_for_record += 1
            if args.max_windows_per_record and count_for_record >= args.max_windows_per_record:
                break

        if index % 200 == 0 or index == len(rows):
            elapsed = time.time() - started
            print(
                f"[features] {index}/{len(rows)} recordings, "
                f"{len(features)} windows, {elapsed:.1f}s"
            )

    matrix = np.stack(features) if features else np.zeros((0, 0), dtype=np.float32)
    result = {
        "x": matrix,
        "y": np.asarray(labels, dtype=np.int8),
        "subject": np.asarray(subjects),
        "record": np.asarray(records),
        "skipped_silent": np.asarray([skipped_silent], dtype=np.int64),
        "skipped_unannotated": np.asarray([skipped_unannotated], dtype=np.int64),
    }
    return result


def patient_level(
    probabilities: np.ndarray, labels: np.ndarray, subjects: np.ndarray, threshold: float
) -> tuple[dict, np.ndarray, np.ndarray]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for subject, probability in zip(subjects, probabilities):
        grouped[subject].append(float(probability))
    subject_ids = sorted(grouped)
    aggregated = np.asarray([np.mean(grouped[subject]) for subject in subject_ids])
    subject_true = np.asarray(
        [int(labels[subjects == subject][0]) for subject in subject_ids], dtype=int
    )
    metrics = binary_metrics(subject_true, aggregated, threshold)
    metrics["auroc_ci95"] = bootstrap_ci(subject_true, aggregated, "auroc")
    metrics["balanced_accuracy_ci95"] = bootstrap_ci(
        subject_true, aggregated, "balanced_accuracy", threshold
    )
    return metrics, subject_true, aggregated


def split_indices(subjects: np.ndarray, rows: list[dict], split: str) -> np.ndarray:
    split_subjects = {row["subject_id"] for row in rows if row["split"] == split}
    return np.asarray([index for index, subject in enumerate(subjects) if subject in split_subjects])


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    cache_path = Path(args.cache)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = load_recordings(data_dir)
    print(f"[data] {len(rows)} labelled recordings in train/val/test")

    if cache_path.exists() and not args.rebuild_cache:
        cached = np.load(cache_path, allow_pickle=True)
        dataset = {key: cached[key] for key in cached.files}
        print(f"[data] loaded feature cache: {cache_path}")
    else:
        dataset = build_features(rows, args)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache_path, **dataset)
        print(f"[data] wrote feature cache: {cache_path}")

    x = dataset["x"].astype(np.float64)
    y = dataset["y"].astype(int)
    subjects = dataset["subject"].astype(str)
    print(f"[data] feature matrix: {x.shape}, positives={int(y.sum())}")

    train_idx = split_indices(subjects, rows, "train")
    val_idx = split_indices(subjects, rows, "val")
    test_idx = split_indices(subjects, rows, "test")
    scaler = StandardScaler().fit(x[train_idx])
    x_scaled = scaler.transform(x)

    model = LogisticRegression(
        epochs=args.epochs, learning_rate=args.learning_rate, l2=args.l2
    ).fit(x_scaled[train_idx], y[train_idx])

    probabilities = model.predict_proba(x_scaled)
    val_threshold, val_score = best_threshold(y[val_idx], probabilities[val_idx])

    report: dict = {
        "task": "CirCor murmur Present vs Absent, subject-level split",
        "model": "log-mel summary + L2 logistic regression (NumPy)",
        "window_sec": args.window_sec,
        "hop_sec": args.hop_sec,
        "feature_count": int(x.shape[1]),
        "windows": int(x.shape[0]),
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "l2": args.l2,
        "silent_windows_skipped": int(dataset["skipped_silent"][0]),
        "window_selection": args.window_selection,
        "min_annotated_fraction": args.min_annotated_fraction,
        "unannotated_windows_skipped": int(dataset["skipped_unannotated"][0]),
        "chosen_threshold": val_threshold,
        "val_balanced_accuracy_at_threshold": val_score,
        "splits": {
            "window_level": {
                "train": binary_metrics(y[train_idx], probabilities[train_idx]),
                "val": binary_metrics(y[val_idx], probabilities[val_idx], val_threshold),
                "test": binary_metrics(y[test_idx], probabilities[test_idx], val_threshold),
            }
        },
    }
    for split, index in (("train", train_idx), ("val", val_idx), ("test", test_idx)):
        metrics, _, _ = patient_level(
            probabilities[index], y[index], subjects[index], val_threshold
        )
        report["splits"].setdefault("patient_level", {})[split] = metrics

    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_markdown(out_dir / "report.md", report)
    print(json.dumps(report["splits"], indent=2, ensure_ascii=False))
    print(f"[report] {out_dir / 'report.md'}")


def write_markdown(path: Path, report: dict) -> None:
    lines = [
        "# CirCor baseline report: log-mel + logistic regression",
        "",
        f"- Task: {report['task']}",
        f"- Model: {report['model']}",
        f"- Window: {report['window_sec']} s, hop: {report['hop_sec']} s",
        f"- Window selection: {report['window_selection']} "
        f"(min annotated fraction {report['min_annotated_fraction']})",
        f"- Windows: {report['windows']}, features: {report['feature_count']}",
        f"- Threshold selected on validation: {report['chosen_threshold']:.4f}",
        "",
        "| Level | Split | n | Accuracy | Balanced acc. | Sensitivity | Specificity | AUROC | F1 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for level, splits in report["splits"].items():
        for split, metrics in splits.items():
            lines.append(
                "| {level} | {split} | {n} | {accuracy:.3f} | {balanced_accuracy:.3f} | "
                "{sensitivity:.3f} | {specificity:.3f} | {auroc:.3f} | {f1:.3f} |".format(
                    level=level, split=split, **metrics
                )
            )
    lines.append("")
    lines.append(
        "This is a first reference baseline, not a frozen model. The next step is a "
        "small 1D CNN trained on the same patient-level split."
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
