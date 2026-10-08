#!/usr/bin/env python3
"""Patient-level stratified group K-fold cross-validation.

Every subject appears in exactly one test fold. For each fold the remaining
subjects are split into a training set and a small validation set used only to
choose the decision threshold. Supported models:

- ``logreg``: log-mel summary features + L2 logistic regression (NumPy).
- ``cnn``: the small 1D CNN trained on raw windows (PyTorch, CPU).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
import types
from collections import defaultdict
from pathlib import Path

import numpy as np

from hs_features import (
    design_bandpass_fir,
    filtfilt,
    iter_windows,
    read_wav_mono,
    window_feature_vector,
)
from hs_metrics import best_threshold, binary_metrics, bootstrap_ci
from hs_models import LogisticRegression, StandardScaler
from hs_segments import annotated_window_starts, load_segments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/processed/circor")
    parser.add_argument("--out-dir", default="data/reports/circor-cv-logreg")
    parser.add_argument("--model", choices=("logreg", "cnn"), default="logreg")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--window-sec", type=float, default=2.0)
    parser.add_argument("--hop-sec", type=float, default=1.0)
    parser.add_argument("--max-windows-per-record", type=int, default=4)
    parser.add_argument("--window-selection", choices=("all", "annotated"), default="all")
    parser.add_argument("--min-annotated-fraction", type=float, default=0.5)
    parser.add_argument("--silence-rms", type=float, default=1e-4)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=20261008)
    # logistic regression
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--l2", type=float, default=1e-3)
    # cnn
    parser.add_argument("--cnn-epochs", type=int, default=30)
    parser.add_argument("--cnn-patience", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--cnn-learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    return parser.parse_args()


def load_rows(data_dir: Path) -> list[dict]:
    with (data_dir / "recordings.csv").open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle)]
    return [
        row
        for row in rows
        if row["label"] in ("0", "1") and row["split"] in ("train", "val", "test")
    ]


def collect(rows: list[dict], args: argparse.Namespace) -> dict:
    feature_mode = args.model == "logreg"
    items: list[np.ndarray] = []
    labels: list[int] = []
    subjects: list[str] = []
    records: list[str] = []
    skipped_silent = 0
    skipped_unannotated = 0
    started = time.time()

    for index, row in enumerate(rows, start=1):
        signal, sample_rate = read_wav_mono(row["wav_path"])
        signal = filtfilt(signal, design_bandpass_fir(sample_rate))
        window_samples = int(round(args.window_sec * sample_rate))
        hop_samples = int(round(args.hop_sec * sample_rate))
        windows = [
            (start, window)
            for start, window in iter_windows(signal, window_samples, hop_samples)
        ]
        if args.window_selection == "annotated":
            segments = load_segments(row.get("tsv_path", ""))
            allowed = set(
                annotated_window_starts(
                    len(signal),
                    segments,
                    sample_rate,
                    window_samples,
                    hop_samples,
                    args.min_annotated_fraction,
                ).tolist()
            )
            kept = []
            for start, window in windows:
                if start in allowed:
                    kept.append((start, window))
                else:
                    skipped_unannotated += 1
            windows = kept
        if args.max_windows_per_record and len(windows) > args.max_windows_per_record:
            positions = np.linspace(
                0, len(windows) - 1, args.max_windows_per_record
            ).round().astype(int)
            windows = [windows[position] for position in positions]

        for _, window in windows:
            if float(np.sqrt(np.mean(window**2))) < args.silence_rms:
                skipped_silent += 1
                continue
            if feature_mode:
                items.append(window_feature_vector(window, sample_rate))
            else:
                items.append(window.astype(np.float16))
            labels.append(int(row["label"]))
            subjects.append(row["subject_id"])
            records.append(row["record_id"])

        if index % 250 == 0 or index == len(rows):
            print(
                f"[collect] {index}/{len(rows)} recordings, {len(items)} windows, "
                f"{time.time() - started:.0f}s"
            )

    return {
        "x": np.stack(items) if items else np.zeros((0, 0), dtype=np.float32),
        "y": np.asarray(labels, dtype=np.int8),
        "subject": np.asarray(subjects),
        "record": np.asarray(records),
        "skipped_silent": skipped_silent,
        "skipped_unannotated": skipped_unannotated,
    }


def subject_labels(dataset: dict) -> dict[str, int]:
    labels: dict[str, int] = {}
    for subject, label in zip(dataset["subject"], dataset["y"]):
        labels[str(subject)] = int(label)
    return labels


def stratified_folds(
    labels: dict[str, int], n_folds: int, seed: int
) -> list[set[str]]:
    rng = random.Random(seed)
    folds = [set() for _ in range(n_folds)]
    for label in (0, 1):
        subjects = sorted(subject for subject, value in labels.items() if value == label)
        rng.shuffle(subjects)
        for position, subject in enumerate(subjects):
            folds[position % n_folds].add(subject)
    return folds


def split_train_val(
    pool: set[str], labels: dict[str, int], val_ratio: float, seed: int
) -> tuple[set[str], set[str]]:
    rng = random.Random(seed)
    train: set[str] = set()
    validation: set[str] = set()
    for label in (0, 1):
        subjects = sorted(subject for subject in pool if labels[subject] == label)
        rng.shuffle(subjects)
        n_val = max(1, int(round(len(subjects) * val_ratio))) if len(subjects) >= 2 else 0
        validation.update(subjects[:n_val])
        train.update(subjects[n_val:])
    return train, validation


def indices_for(subjects: np.ndarray, subset: set[str]) -> np.ndarray:
    if not subset:
        return np.zeros(0, dtype=np.int64)
    return np.flatnonzero(np.isin(subjects, list(subset)))


def patient_probabilities(
    probability: np.ndarray, y: np.ndarray, subjects: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for subject, value in zip(subjects, probability):
        grouped[str(subject)].append(float(value))
    ids = sorted(grouped)
    aggregated = np.asarray([np.mean(grouped[subject]) for subject in ids])
    truth = np.asarray([int(y[subjects == subject][0]) for subject in ids], dtype=int)
    return truth, aggregated


def evaluate_split(
    probability: np.ndarray,
    y: np.ndarray,
    subjects: np.ndarray,
    threshold: float,
) -> tuple[dict, dict]:
    window_metrics = binary_metrics(y, probability, threshold)
    truth, aggregated = patient_probabilities(probability, y, subjects)
    patient_metrics = binary_metrics(truth, aggregated, threshold)
    return window_metrics, patient_metrics


def cnn_args(args: argparse.Namespace) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        epochs=args.cnn_epochs,
        patience=args.cnn_patience,
        batch_size=args.batch_size,
        learning_rate=args.cnn_learning_rate,
        weight_decay=args.weight_decay,
        seed=args.seed,
    )


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows(data_dir)
    print(f"[cv] {len(rows)} labelled recordings")
    dataset = collect(rows, args)
    print(
        f"[cv] {len(dataset['y'])} windows, "
        f"skipped_silent={dataset['skipped_silent']}, "
        f"skipped_unannotated={dataset['skipped_unannotated']}"
    )
    if len(dataset["y"]) == 0:
        raise SystemExit("no windows collected")

    labels = subject_labels(dataset)
    folds = stratified_folds(labels, args.folds, args.seed)
    pooled_probability: list[float] = []
    pooled_truth: list[int] = []
    per_fold: list[dict] = []

    for fold_index, test_subjects in enumerate(folds):
        pool = set(labels) - test_subjects
        train_subjects, val_subjects = split_train_val(
            pool, labels, args.val_ratio, args.seed + fold_index
        )
        train_idx = indices_for(dataset["subject"], train_subjects)
        val_idx = indices_for(dataset["subject"], val_subjects)
        test_idx = indices_for(dataset["subject"], test_subjects)
        if len(train_idx) == 0 or len(val_idx) == 0 or len(test_idx) == 0:
            raise SystemExit(
                f"fold {fold_index} has an empty train/val/test window set"
            )

        if args.model == "logreg":
            scaler = StandardScaler().fit(dataset["x"][train_idx])
            model = LogisticRegression(
                epochs=args.epochs,
                learning_rate=args.learning_rate,
                l2=args.l2,
                seed=args.seed,
            ).fit(scaler.transform(dataset["x"][train_idx]), dataset["y"][train_idx])
            val_probability = model.predict_proba(scaler.transform(dataset["x"][val_idx]))
            test_probability = model.predict_proba(scaler.transform(dataset["x"][test_idx]))
        else:
            from train_cnn import predict, train_model

            cache = {
                "train": {
                    "x": dataset["x"][train_idx],
                    "y": dataset["y"][train_idx],
                    "subject": dataset["subject"][train_idx],
                    "record": dataset["record"][train_idx],
                },
                "val": {
                    "x": dataset["x"][val_idx],
                    "y": dataset["y"][val_idx],
                    "subject": dataset["subject"][val_idx],
                    "record": dataset["record"][val_idx],
                },
            }
            model, _ = train_model(cache, cnn_args(args))
            val_probability = predict(model, dataset["x"][val_idx])
            test_probability = predict(model, dataset["x"][test_idx])

        threshold, val_score = best_threshold(
            dataset["y"][val_idx], val_probability
        )
        window_metrics, patient_metrics = evaluate_split(
            test_probability,
            dataset["y"][test_idx],
            dataset["subject"][test_idx],
            threshold,
        )
        truth, aggregated = patient_probabilities(
            test_probability, dataset["y"][test_idx], dataset["subject"][test_idx]
        )
        pooled_probability.extend(aggregated.tolist())
        pooled_truth.extend(truth.tolist())
        per_fold.append(
            {
                "fold": fold_index,
                "threshold": threshold,
                "val_balanced_accuracy_at_threshold": val_score,
                "n_train_subjects": len(train_subjects),
                "n_val_subjects": len(val_subjects),
                "n_test_subjects": len(test_subjects),
                "n_test_windows": int(len(test_idx)),
                "window": window_metrics,
                "patient": patient_metrics,
            }
        )
        print(
            f"[fold {fold_index}] test_subjects={len(test_subjects)} "
            f"threshold={threshold:.4f} "
            f"window_auroc={window_metrics['auroc']:.3f} "
            f"patient_auroc={patient_metrics['auroc']:.3f} "
            f"patient_ba={patient_metrics['balanced_accuracy']:.3f}"
        )

    def aggregate(key: str, field: str) -> dict:
        values = np.asarray([fold[key][field] for fold in per_fold], dtype=float)
        return {
            "mean": float(np.nanmean(values)),
            "std": float(np.nanstd(values)),
            "values": values.tolist(),
        }

    pooled_patient = binary_metrics(
        np.asarray(pooled_truth), np.asarray(pooled_probability), 0.5
    )
    pooled_patient["auroc_ci95"] = bootstrap_ci(
        np.asarray(pooled_truth), np.asarray(pooled_probability), "auroc"
    )
    pooled_patient["balanced_accuracy_ci95"] = bootstrap_ci(
        np.asarray(pooled_truth),
        np.asarray(pooled_probability),
        "balanced_accuracy",
        0.5,
    )
    report = {
        "task": "CirCor murmur Present vs Absent",
        "model": args.model,
        "folds": args.folds,
        "window_selection": args.window_selection,
        "min_annotated_fraction": args.min_annotated_fraction,
        "windows_total": int(len(dataset["y"])),
        "subjects_total": len(labels),
        "skipped_silent": dataset["skipped_silent"],
        "skipped_unannotated": dataset["skipped_unannotated"],
        "per_fold": per_fold,
        "aggregate": {
            "patient_auroc": aggregate("patient", "auroc"),
            "patient_balanced_accuracy": aggregate("patient", "balanced_accuracy"),
            "patient_sensitivity": aggregate("patient", "sensitivity"),
            "patient_specificity": aggregate("patient", "specificity"),
            "window_auroc": aggregate("window", "auroc"),
        },
        "pooled_patient_at_0.5": pooled_patient,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_markdown(out_dir / "report.md", report)
    print(json.dumps(report["aggregate"], indent=2, ensure_ascii=False))
    print(f"[cv] wrote {out_dir / 'report.json'}")


def write_markdown(path: Path, report: dict) -> None:
    lines = [
        "# Patient-level cross-validation report",
        "",
        f"- Model: {report['model']}",
        f"- Folds: {report['folds']}",
        f"- Window selection: {report['window_selection']}",
        f"- Windows: {report['windows_total']}, subjects: {report['subjects_total']}",
        "",
        "| Fold | Test subjects | Window AUROC | Patient AUROC | Patient BA | Sensitivity | Specificity |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for fold in report["per_fold"]:
        lines.append(
            "| {fold} | {n_test_subjects} | {wa:.3f} | {pa:.3f} | {pb:.3f} | "
            "{sen:.3f} | {spe:.3f} |".format(
                fold=fold["fold"],
                n_test_subjects=fold["n_test_subjects"],
                wa=fold["window"]["auroc"],
                pa=fold["patient"]["auroc"],
                pb=fold["patient"]["balanced_accuracy"],
                sen=fold["patient"]["sensitivity"],
                spe=fold["patient"]["specificity"],
            )
        )
    aggregate = report["aggregate"]
    lines.extend(
        [
            "",
            "## Aggregate (mean ± std across folds)",
            "",
            f"- Patient AUROC: {aggregate['patient_auroc']['mean']:.3f} ± "
            f"{aggregate['patient_auroc']['std']:.3f}",
            f"- Patient balanced accuracy: "
            f"{aggregate['patient_balanced_accuracy']['mean']:.3f} ± "
            f"{aggregate['patient_balanced_accuracy']['std']:.3f}",
            f"- Window AUROC: {aggregate['window_auroc']['mean']:.3f} ± "
            f"{aggregate['window_auroc']['std']:.3f}",
            "",
            f"- Pooled patient AUROC at 0.5: "
            f"{report['pooled_patient_at_0.5']['auroc']:.3f} "
            f"(95% CI {report['pooled_patient_at_0.5']['auroc_ci95']['low']:.3f}–"
            f"{report['pooled_patient_at_0.5']['auroc_ci95']['high']:.3f})",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
