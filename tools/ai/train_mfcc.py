#!/usr/bin/env python3
"""Train and evaluate the MFCC + delta baseline with patient-level 5-fold CV.

This is the second of the three comparison arms in ``docs/progress-2026-10-17.md``
(section 5, item 2): the current 1D CNN (``cross_validate.py --model cnn``), this
MFCC + delta + logistic-regression model, and the 40-band log-mel logistic
regression (``cross_validate.py --model logreg``).

The patient-level protocol is deliberately *shared* with ``cross_validate.py``:
subject-level stratified folds, a validation split used only to pick the
decision threshold, mean-of-window probabilities per patient, mean +/- std
across folds and a pooled bootstrap CI. Those helpers are imported from
``cross_validate`` rather than re-implemented, so the three arms cannot drift
apart. Only the feature front-end differs (``hs_mfcc`` instead of
``hs_features.window_feature_vector``). Pass ``--splits-file`` with the frozen
``splits.csv`` from ``tools/ai/freeze_splits.py`` to pin the patient folds
explicitly; the report then records the split fingerprint, which is what makes
the "same patient-level protocol" claim checkable instead of assumed.

Usage (synthetic smoke test, numbers are pipeline validation only):

    python tools/ai/synthetic_pcg.py --out-dir /tmp/mfcc-demo --subjects 12
    python tools/ai/train_mfcc.py --data-dir /tmp/mfcc-demo --out-dir /tmp/mfcc-demo/report
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from cross_validate import (
    indices_for,
    load_rows,
    patient_probabilities,
    split_train_val,
    stratified_folds,
    subject_labels,
)
from hs_features import design_bandpass_fir, filtfilt, iter_windows, read_wav_mono
from hs_metrics import best_threshold, binary_metrics, bootstrap_ci
from hs_mfcc import (
    AGGREGATIONS,
    DEFAULT_DELTA_WIDTH,
    DEFAULT_HOP_LENGTH,
    DEFAULT_N_FFT,
    DEFAULT_N_MELS,
    FEATURE_SETS,
    mfcc_feature_dim,
    mfcc_window_vector,
)
from hs_models import LogisticRegression, StandardScaler
from hs_segments import annotated_window_starts, load_segments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/processed/circor")
    parser.add_argument("--out-dir", default="data/reports/circor-cv-mfcc")
    parser.add_argument(
        "--cache",
        default="",
        help="Optional .npz feature cache path; empty (default) disables caching.",
    )
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--window-sec", type=float, default=2.0)
    parser.add_argument("--hop-sec", type=float, default=1.0)
    parser.add_argument("--max-windows-per-record", type=int, default=4)
    parser.add_argument(
        "--window-selection",
        choices=("all", "annotated"),
        default="all",
        help="Use every window, or only windows covered by CirCor segmentation labels.",
    )
    parser.add_argument("--min-annotated-fraction", type=float, default=0.5)
    parser.add_argument("--silence-rms", type=float, default=1e-4)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--n-folds", "--folds", dest="n_folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261008)
    parser.add_argument(
        "--splits-file",
        default=None,
        help=(
            "Optional frozen splits.csv from tools/ai/freeze_splits.py. When given, "
            "the patient folds come from that file (and its sha256 fingerprint is "
            "recorded in the report) instead of being re-derived from --seed, so this "
            "arm can be proven to use the same patient folds as the CNN and log-mel "
            "arms. Requires tools/ai/hs_splits.py; default keeps the seed-derived "
            "folds of tools/ai/cross_validate.py."
        ),
    )
    # MFCC front-end
    parser.add_argument("--n-mfcc", type=int, default=13)
    parser.add_argument("--n-mels", type=int, default=DEFAULT_N_MELS)
    parser.add_argument("--n-fft", type=int, default=DEFAULT_N_FFT)
    parser.add_argument("--hop-length", type=int, default=DEFAULT_HOP_LENGTH)
    parser.add_argument("--delta-width", type=int, default=DEFAULT_DELTA_WIDTH)
    parser.add_argument(
        "--no-delta-delta",
        action="store_true",
        help="Drop the second-order differences (78 features instead of 117).",
    )
    # logistic regression
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--l2", type=float, default=1e-3)
    return parser.parse_args()


def feature_config(args: argparse.Namespace, feature_count: int | None = None) -> dict:
    """Exact description of the feature configuration used for this run."""
    include_delta_delta = not args.no_delta_delta
    config = {
        "name": "mfcc_delta_logreg",
        "n_mfcc": args.n_mfcc,
        "n_mels": args.n_mels,
        "n_fft": args.n_fft,
        "hop_length": args.hop_length,
        "delta_width": args.delta_width,
        "feature_sets": list(FEATURE_SETS) if include_delta_delta else list(FEATURE_SETS[:2]),
        "aggregations": list(AGGREGATIONS),
        "include_delta_delta": include_delta_delta,
        "feature_count_formula": "n_mfcc * len(aggregations) * n_feature_sets",
        "feature_count_expected": mfcc_feature_dim(args.n_mfcc, include_delta_delta),
        "layout": "per set: mean(n_mfcc) | std(n_mfcc) | p90(n_mfcc)",
        "window_normalization": "mean removal + RMS normalization, then log-mel (dB via log10)",
        "delta_formula": (
            "d[t] = sum_{n=1..W} n*(c[t+n]-c[t-n]) / (2*sum n^2), odd-reflect boundaries"
        ),
        "bandpass_hz": [20.0, 1000.0],
        "mel_band_hz": [20.0, 1000.0],
        "silence_rms": args.silence_rms,
    }
    if feature_count is not None:
        config["feature_count"] = int(feature_count)
        config["feature_count_matches_formula"] = bool(
            int(feature_count) == config["feature_count_expected"]
        )
    return config


def read_dataset_kind(data_dir: Path) -> str:
    """Read the ``dataset`` label that ``synthetic_pcg.py`` writes to summary.json."""
    summary_path = data_dir / "summary.json"
    if not summary_path.exists():
        return "unknown"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    return str(summary.get("dataset", "unknown"))


def collect(rows: list[dict], args: argparse.Namespace) -> dict:
    """Extract MFCC window vectors with the same window policy as ``cross_validate``."""
    items: list[np.ndarray] = []
    labels: list[int] = []
    subjects: list[str] = []
    records: list[str] = []
    skipped_silent = 0
    skipped_unannotated = 0
    sample_rates: set[int] = set()
    started = time.time()

    for index, row in enumerate(rows, start=1):
        signal, sample_rate = read_wav_mono(row["wav_path"])
        sample_rates.add(int(sample_rate))
        signal = filtfilt(signal, design_bandpass_fir(sample_rate))
        window_samples = int(round(args.window_sec * sample_rate))
        hop_samples = int(round(args.hop_sec * sample_rate))
        windows = list(iter_windows(signal, window_samples, hop_samples))
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
            positions = (
                np.linspace(0, len(windows) - 1, args.max_windows_per_record)
                .round()
                .astype(int)
            )
            windows = [windows[position] for position in positions]

        for _, window in windows:
            if float(np.sqrt(np.mean(window**2))) < args.silence_rms:
                skipped_silent += 1
                continue
            items.append(
                mfcc_window_vector(
                    window,
                    sample_rate,
                    n_mfcc=args.n_mfcc,
                    n_fft=args.n_fft,
                    hop_length=args.hop_length,
                    n_mels=args.n_mels,
                    include_delta_delta=not args.no_delta_delta,
                    width=args.delta_width,
                )
            )
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
        "sample_rates": sorted(sample_rates),
    }


def resolve_folds(
    labels: dict[str, int], args: argparse.Namespace
) -> tuple[list[set[str]], dict]:
    """Return the test-fold patient sets plus a description of where they came from.

    Default path: the exact helper used by ``cross_validate.py`` (seed-derived
    stratified folds). With ``--splits-file``: a frozen ``splits.csv``, which is
    what makes the "same patient folds" claim across the three comparison arms
    verifiable rather than an assumption. ``hs_splits`` is imported lazily so the
    default path has no dependency on it.
    """
    if not args.splits_file:
        folds = stratified_folds(labels, args.n_folds, args.seed)
        return folds, {
            "source": "seed-derived (cross_validate.stratified_folds)",
            "seed": args.seed,
            "n_folds": args.n_folds,
        }
    try:
        from hs_splits import fold_sets, load_splits, splits_fingerprint, validate_splits
    except ImportError as exc:  # pragma: no cover - only when hs_splits is absent
        raise SystemExit(f"--splits-file requires tools/ai/hs_splits.py: {exc}") from exc
    try:
        assignment = load_splits(args.splits_file)
        validate_splits(assignment, labels, args.n_folds)
    except ValueError as exc:
        raise SystemExit(f"invalid --splits-file {args.splits_file}: {exc}") from exc
    folds = fold_sets(assignment, args.n_folds)
    return folds, {
        "source": "frozen splits file (hs_splits)",
        "splits_file": str(args.splits_file),
        "splits_fingerprint": splits_fingerprint(assignment),
        "n_folds": args.n_folds,
    }


def aggregate_over_folds(per_fold: list[dict], level: str, field: str) -> dict:
    values = np.asarray([fold[level][field] for fold in per_fold], dtype=float)
    return {
        "mean": float(np.nanmean(values)),
        "std": float(np.nanstd(values)),
        "values": [float(value) for value in values],
    }


def main() -> None:
    args = parse_args()
    if args.n_folds < 2:
        raise SystemExit("--n-folds must be >= 2")
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_rows(data_dir)
    print(f"[mfcc-cv] {len(rows)} labelled recordings in {data_dir}")
    if not rows:
        raise SystemExit(f"no labelled recordings found in {data_dir / 'recordings.csv'}")
    dataset_kind = read_dataset_kind(data_dir)
    is_synthetic = "synthetic" in dataset_kind.lower()
    if is_synthetic:
        print(
            "[mfcc-cv] WARNING: synthetic dataset - numbers are pipeline validation "
            "only, not clinical evidence"
        )

    cache_path = Path(args.cache) if args.cache else None
    if cache_path is not None and cache_path.exists() and not args.rebuild_cache:
        cached = np.load(cache_path, allow_pickle=True)
        dataset = {key: cached[key] for key in cached.files}
        dataset["sample_rates"] = [
            int(value) for value in np.atleast_1d(dataset.get("sample_rates", [0]))
        ]
        dataset.setdefault("skipped_silent", 0)
        dataset.setdefault("skipped_unannotated", 0)
        print(f"[mfcc-cv] loaded feature cache: {cache_path}")
    else:
        dataset = collect(rows, args)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                cache_path,
                x=dataset["x"],
                y=dataset["y"],
                subject=dataset["subject"],
                record=dataset["record"],
                sample_rates=np.asarray(dataset["sample_rates"], dtype=np.int64),
            )
            print(f"[mfcc-cv] wrote feature cache: {cache_path}")

    x = dataset["x"].astype(np.float64)
    y = dataset["y"].astype(int)
    subjects = dataset["subject"].astype(str)
    print(
        f"[mfcc-cv] {len(y)} windows, features={x.shape[1]}, "
        f"skipped_silent={dataset['skipped_silent']}, "
        f"skipped_unannotated={dataset['skipped_unannotated']}"
    )
    if len(y) == 0:
        raise SystemExit("no windows collected")

    labels = subject_labels(dataset)
    folds, fold_source = resolve_folds(labels, args)
    print(f"[mfcc-cv] folds: {fold_source}")
    pooled_probability: list[float] = []
    pooled_truth: list[int] = []
    per_fold: list[dict] = []
    max_train_test_overlap = 0
    max_val_test_overlap = 0

    for fold_index, test_subjects in enumerate(folds):
        pool = set(labels) - test_subjects
        train_subjects, val_subjects = split_train_val(
            pool, labels, args.val_ratio, args.seed + fold_index
        )
        if train_subjects & test_subjects or val_subjects & test_subjects:
            raise SystemExit(f"fold {fold_index}: patient leakage between train/val and test")
        max_train_test_overlap = max(
            max_train_test_overlap, len(train_subjects & test_subjects)
        )
        max_val_test_overlap = max(max_val_test_overlap, len(val_subjects & test_subjects))

        train_idx = indices_for(subjects, train_subjects)
        val_idx = indices_for(subjects, val_subjects)
        test_idx = indices_for(subjects, test_subjects)
        if len(train_idx) == 0 or len(val_idx) == 0 or len(test_idx) == 0:
            raise SystemExit(f"fold {fold_index} has an empty train/val/test window set")

        scaler = StandardScaler().fit(x[train_idx])
        model = LogisticRegression(
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            l2=args.l2,
            seed=args.seed,
        ).fit(scaler.transform(x[train_idx]), y[train_idx])
        val_probability = model.predict_proba(scaler.transform(x[val_idx]))
        test_probability = model.predict_proba(scaler.transform(x[test_idx]))

        threshold, val_score = best_threshold(y[val_idx], val_probability)
        window_metrics = binary_metrics(y[test_idx], test_probability, threshold)
        truth, aggregated = patient_probabilities(
            test_probability, y[test_idx], subjects[test_idx]
        )
        patient_metrics = binary_metrics(truth, aggregated, threshold)
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
                "n_train_windows": int(len(train_idx)),
                "n_val_windows": int(len(val_idx)),
                "n_test_windows": int(len(test_idx)),
                "test_subjects": sorted(test_subjects),
                "train_subjects": sorted(train_subjects),
                "val_subjects": sorted(val_subjects),
                "window": window_metrics,
                "patient": patient_metrics,
            }
        )
        print(
            f"[fold {fold_index}] train/val/test subjects="
            f"{len(train_subjects)}/{len(val_subjects)}/{len(test_subjects)} "
            f"threshold={threshold:.4f} "
            f"window_auroc={window_metrics['auroc']:.3f} "
            f"patient_auroc={patient_metrics['auroc']:.3f} "
            f"patient_ba={patient_metrics['balanced_accuracy']:.3f} "
            f"confusion={patient_metrics['confusion']}"
        )

    pooled_truth_array = np.asarray(pooled_truth, dtype=int)
    pooled_probability_array = np.asarray(pooled_probability, dtype=float)
    pooled_patient = binary_metrics(pooled_truth_array, pooled_probability_array, 0.5)
    pooled_patient["auroc_ci95"] = bootstrap_ci(
        pooled_truth_array, pooled_probability_array, "auroc", seed=args.seed + 101
    )
    pooled_patient["balanced_accuracy_ci95"] = bootstrap_ci(
        pooled_truth_array,
        pooled_probability_array,
        "balanced_accuracy",
        0.5,
        seed=args.seed + 102,
    )
    thresholds = np.asarray([fold["threshold"] for fold in per_fold], dtype=float)

    report = {
        "task": "CirCor murmur Present vs Absent, patient-level stratified grouped CV",
        "model": "MFCC + delta + delta-delta summary + StandardScaler + L2 logistic regression (NumPy)",
        "comparison_arm": "MFCC+delta small model (arm 2 of 3; see docs/progress-2026-10-17.md)",
        "protocol": {
            "shared_with": "tools/ai/cross_validate.py (same fold/threshold/aggregation helpers)",
            "folds": args.n_folds,
            "fold_source": fold_source,
            "seed": args.seed,
            "val_ratio": args.val_ratio,
            "window_sec": args.window_sec,
            "hop_sec": args.hop_sec,
            "max_windows_per_record": args.max_windows_per_record,
            "window_selection": args.window_selection,
            "min_annotated_fraction": args.min_annotated_fraction,
            "threshold_selection": "per fold on the validation split, balanced accuracy",
            "patient_aggregation": "mean of window probabilities per subject",
            "pooled_threshold": 0.5,
        },
        "feature_config": feature_config(args, int(x.shape[1])),
        "classifier": {
            "type": "L2 logistic regression (Adam, class-balanced)",
            "epochs": args.epochs,
            "learning_rate": args.learning_rate,
            "l2": args.l2,
            "scaler": "StandardScaler fit on training windows only",
        },
        "data": {
            "recordings": len(rows),
            "windows_total": int(len(y)),
            "subjects_total": len(labels),
            "positives_subjects": int(sum(labels.values())),
            "skipped_silent": int(dataset["skipped_silent"]),
            "skipped_unannotated": int(dataset["skipped_unannotated"]),
            "sample_rates_hz": [int(value) for value in dataset["sample_rates"]],
            "dataset_kind": dataset_kind,
            "is_synthetic": is_synthetic,
        },
        "patient_overlap": {
            "train_test_max": int(max_train_test_overlap),
            "val_test_max": int(max_val_test_overlap),
            "policy": "subject-level folds; every subject is test in exactly one fold",
        },
        "thresholds": {
            "per_fold": [float(value) for value in thresholds],
            "min": float(np.min(thresholds)),
            "max": float(np.max(thresholds)),
            "mean": float(np.mean(thresholds)),
        },
        "per_fold": per_fold,
        "aggregate": {
            "patient_auroc": aggregate_over_folds(per_fold, "patient", "auroc"),
            "patient_balanced_accuracy": aggregate_over_folds(
                per_fold, "patient", "balanced_accuracy"
            ),
            "patient_sensitivity": aggregate_over_folds(per_fold, "patient", "sensitivity"),
            "patient_specificity": aggregate_over_folds(per_fold, "patient", "specificity"),
            "patient_f1": aggregate_over_folds(per_fold, "patient", "f1"),
            "window_auroc": aggregate_over_folds(per_fold, "window", "auroc"),
        },
        "pooled_patient_at_0.5": pooled_patient,
        "evidence_level": (
            "E4 for reproducibility on the machine that produced this report; the dataset "
            "makes it synthetic pipeline validation (not clinical evidence) or CirCor "
            "public-data prototype evidence, depending on --data-dir."
        ),
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_markdown(out_dir / "report.md", report)
    print(json.dumps(report["aggregate"], indent=2, ensure_ascii=False))
    print(
        f"[mfcc-cv] thresholds per fold: "
        f"{', '.join(f'{value:.3f}' for value in thresholds)}"
    )
    print(f"[mfcc-cv] wrote {out_dir / 'report.json'} and {out_dir / 'report.md'}")


def write_markdown(path: Path, report: dict) -> None:
    config = report["feature_config"]
    protocol = report["protocol"]
    lines = [
        "# MFCC + delta baseline: patient-level 5-fold cross-validation",
        "",
        "Comparison arm 2 of 3 from `docs/progress-2026-10-17.md` (section 5, item 2):",
        "1D CNN / **MFCC + delta + logistic regression** / 40-band log-mel logistic regression.",
        "The fold, threshold and patient-aggregation helpers are imported from",
        "`tools/ai/cross_validate.py`, so this arm uses the same patient-level protocol.",
        "",
    ]
    if report["data"]["is_synthetic"]:
        lines.extend(
            [
                "> **Synthetic data - pipeline validation only (E4), NOT clinical evidence.**",
                "> The generator (`tools/ai/synthetic_pcg.py`) adds a broadband 80-600 Hz burst",
                "> during systole, which makes the two classes almost linearly separable;",
                "> AUROC near 1.0 here measures that the harness works, not model quality.",
                "> These numbers must never be compared with CirCor results.",
                "",
            ]
        )
    lines.extend(
        [
            "## Configuration",
            "",
            f"- Model: {report['model']}",
            f"- Task: {report['task']}",
            f"- Feature vector: {config['feature_count']} values = "
            f"{config['n_mfcc']} MFCC x {len(config['aggregations'])} statistics "
            f"({', '.join(config['aggregations'])}) x {len(config['feature_sets'])} sets "
            f"({', '.join(config['feature_sets'])})",
            f"- Layout: {config['layout']}",
            f"- STFT: n_fft={config['n_fft']}, hop_length={config['hop_length']}, "
            f"n_mels={config['n_mels']}, mel band {config['mel_band_hz']} Hz",
            f"- Delta: width W={config['delta_width']}, formula `{config['delta_formula']}`",
            f"- Normalization: {config['window_normalization']}",
            f"- Band-pass: {config['bandpass_hz']} Hz zero-phase FIR before windowing",
            f"- Window: {protocol['window_sec']} s, hop {protocol['hop_sec']} s, "
            f"max {protocol['max_windows_per_record']} windows/record "
            f"(0 = unlimited), selection `{protocol['window_selection']}`",
            f"- Folds: {protocol['folds']}, source `{protocol['fold_source']['source']}`, "
            f"seed {protocol['seed']}, val_ratio {protocol['val_ratio']}",
            *(
                [f"- Frozen split fingerprint: `{protocol['fold_source']['splits_fingerprint']}`"]
                if "splits_fingerprint" in protocol["fold_source"]
                else []
            ),
            f"- Data: {report['data']['windows_total']} windows, "
            f"{report['data']['subjects_total']} subjects "
            f"({report['data']['positives_subjects']} positive), "
            f"skipped silent {report['data']['skipped_silent']}, "
            f"skipped unannotated {report['data']['skipped_unannotated']}, "
            f"dataset `{report['data']['dataset_kind']}`",
            f"- Patient overlap (train/test): {report['patient_overlap']['train_test_max']}",
            "",
            "## Per-fold patient-level results",
        ]
    )
    lines.extend(
        [
            "",
            "| Fold | Train/Val/Test subjects | Threshold | Window AUROC | Patient AUROC | Patient BA | Sens | Spec | TP/TN/FP/FN |",
            "| ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
        ]
    )
    for fold in report["per_fold"]:
        confusion = fold["patient"]["confusion"]
        lines.append(
            "| {fold} | {train}/{val}/{test} | {thr:.3f} | {wa:.3f} | {pa:.3f} | {pb:.3f} | "
            "{sen:.3f} | {spe:.3f} | {tp}/{tn}/{fp}/{fn} |".format(
                fold=fold["fold"],
                train=fold["n_train_subjects"],
                val=fold["n_val_subjects"],
                test=fold["n_test_subjects"],
                thr=fold["threshold"],
                wa=fold["window"]["auroc"],
                pa=fold["patient"]["auroc"],
                pb=fold["patient"]["balanced_accuracy"],
                sen=fold["patient"]["sensitivity"],
                spe=fold["patient"]["specificity"],
                tp=confusion["tp"],
                tn=confusion["tn"],
                fp=confusion["fp"],
                fn=confusion["fn"],
            )
        )
    aggregate = report["aggregate"]
    pooled = report["pooled_patient_at_0.5"]
    pooled_confusion = pooled["confusion"]
    lines.extend(
        [
            "",
            "## Aggregate (mean +/- std across folds)",
            "",
            f"- Patient AUROC: {aggregate['patient_auroc']['mean']:.3f} +/- "
            f"{aggregate['patient_auroc']['std']:.3f} "
            f"(per fold: {', '.join(f'{value:.3f}' for value in aggregate['patient_auroc']['values'])})",
            f"- Patient balanced accuracy: "
            f"{aggregate['patient_balanced_accuracy']['mean']:.3f} +/- "
            f"{aggregate['patient_balanced_accuracy']['std']:.3f}",
            f"- Patient sensitivity: {aggregate['patient_sensitivity']['mean']:.3f} +/- "
            f"{aggregate['patient_sensitivity']['std']:.3f}",
            f"- Patient specificity: {aggregate['patient_specificity']['mean']:.3f} +/- "
            f"{aggregate['patient_specificity']['std']:.3f}",
            f"- Window AUROC: {aggregate['window_auroc']['mean']:.3f} +/- "
            f"{aggregate['window_auroc']['std']:.3f}",
            f"- Thresholds chosen on the validation split: "
            f"min {report['thresholds']['min']:.3f}, "
            f"mean {report['thresholds']['mean']:.3f}, "
            f"max {report['thresholds']['max']:.3f}",
            "",
            "## Pooled patient-level (all folds, threshold 0.5)",
            "",
            f"- AUROC: {pooled['auroc']:.3f} "
            f"(95% CI {pooled['auroc_ci95']['low']:.3f}-{pooled['auroc_ci95']['high']:.3f}, "
            f"{pooled['auroc_ci95']['n_boot']} bootstrap resamples)",
            f"- Balanced accuracy: {pooled['balanced_accuracy']:.3f} "
            f"(95% CI {pooled['balanced_accuracy_ci95']['low']:.3f}-"
            f"{pooled['balanced_accuracy_ci95']['high']:.3f})",
            f"- Sensitivity {pooled['sensitivity']:.3f}, "
            f"specificity {pooled['specificity']:.3f}, F1 {pooled['f1']:.3f}",
            f"- Confusion: TP {pooled_confusion['tp']}, TN {pooled_confusion['tn']}, "
            f"FP {pooled_confusion['fp']}, FN {pooled_confusion['fn']} "
            f"({pooled['n']} patients)",
            "",
            "## Notes and limits",
            "",
            "- Cross-fold mean is the primary discrimination estimate; the pooled number is "
            "for calibration checking, because each fold picks its own threshold.",
            "- Per-fold thresholds and probabilities are not calibrated for deployment; a "
            "calibration layer is still required before fixing an operating point.",
            "- Evidence level: E4 (locally reproducible CV). If `--data-dir` points at the "
            "synthetic dataset these numbers are **pipeline validation only, not clinical "
            "evidence** and must not be compared with CirCor results.",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
