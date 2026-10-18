#!/usr/bin/env python3
"""Diagnose why static INT8 quantization degrades the small CNN.

The first full-fold measurement showed a mean patient-AUROC drop of 0.11 (one
fold losing 0.26), far beyond the 0.03 ADR-007 criterion. Before concluding
"INT8 is unusable", this script separates the model from the *quantizer setup*:

- calibration set size (how many windows)
- calibration sampling (uniform linspace over the cache vs a random subsample)
- calibration method (MinMax vs Percentile)
- quant format (QOperator vs QDQ)
- per-channel vs per-tensor weight quantization

It re-uses an already exported ``model-fp32.onnx`` and the window cache written
by ``train_cnn.py`` (``windows-{train,test}.npy`` + meta), so it does not
retrain. FP32 is the reference; every configuration is scored on the same test
windows with the same patient aggregation (sigmoid per window, then the mean
probability per subject, exactly like ``train_cnn.patient_level``).

LEAK GUARD
----------
``data/processed/circor/windows-{train,test}.npy`` is a *single* mutable cache:
the last ``train_cnn.py`` run overwrites it for whichever fold it trained. If
this script pairs fold K's ONNX with another fold's cached test windows, the
evaluation leaks (fold K trained on those subjects). Before scoring anything,
the script therefore

1. loads the frozen ``splits.csv`` (same loader/fingerprint as ``train_cnn``),
2. requires the cached test subjects to be *exactly* fold K's test subjects,
3. requires the cached train/val subjects to be disjoint from them,
4. when a sibling ``report.json`` exists, requires its ``folds_source.fold``,
   fingerprint, window counts and FP32 patient AUROC to agree with the cache,

and refuses to run if any of those fail. Pass ``--fold`` explicitly; if it is
omitted the fold is read from the sibling ``report.json``.

Usage:
    python tools/ai/requantize.py --onnx data/reports/int8-cv/fold3/model-fp32.onnx \
        --data-dir data/processed/circor --fold 3 \
        --out data/reports/int8-calibration-sweep/fold3.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hs_splits import load_splits, splits_fingerprint  # noqa: E402

#: Frozen CirCor split fingerprint this workspace publishes against. Recorded in
#: every artefact so a re-frozen ``splits.csv`` cannot be compared silently.
EXPECTED_CIROR_FINGERPRINT = (
    "1f212dea53b9184c91fe8184bf1e5e3e799083065cdc69f436b23b6fa5509a47"
)

#: ORT's own default is 99.999, which for a few hundred windows is numerically
#: indistinguishable from MinMax. 99.99 makes the "percentile" arm an actual
#: clipping decision instead of a no-op.
DEFAULT_PERCENTILE = 99.99

#: Defaults of the corrected calibration recipe, shared with ``train_cnn.py`` so
#: a report and this diagnostic sample the same windows.
DEFAULT_CALIBRATION_SIZE = 200
DEFAULT_CALIBRATION_SEED = 20261018


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onnx", required=True, help="exported FP32 ONNX model")
    parser.add_argument("--data-dir", required=True,
                        help="directory holding windows-{train,test}.npy and meta")
    parser.add_argument("--splits-file", default="",
                        help="frozen splits.csv (default: <data-dir>/splits.csv)")
    parser.add_argument("--fold", type=int, default=None,
                        help="fold whose test windows the cache must contain "
                             "(default: read from the sibling report.json)")
    parser.add_argument("--out", default="")
    parser.add_argument("--calib-sizes", default=f"{DEFAULT_CALIBRATION_SIZE},1000")
    parser.add_argument("--modes", default="linspace,random")
    parser.add_argument("--methods", default="minmax,percentile")
    parser.add_argument("--formats", default="qoperator,qdq")
    parser.add_argument("--percentile", type=float, default=DEFAULT_PERCENTILE,
                        help=f"CalibPercentile for the percentile method "
                             f"(default {DEFAULT_PERCENTILE}; ORT default is 99.999)")
    parser.add_argument("--weight-granularity", choices=("per_channel", "per_tensor"),
                        default="per_channel")
    parser.add_argument("--activation-types", default="quint8",
                        help="comma list of quint8,int8. quint8 reproduces "
                             "hs_cnn.quantize_onnx_static (the published pipeline); "
                             "int8 is the symmetric activation type an ESP-DL/TFLite "
                             "Micro integer kernel uses")
    parser.add_argument("--calib-source", choices=("train", "test"), default="train",
                        help="DIAGNOSTIC ONLY: 'test' calibrates on the fold's own test "
                             "windows. That is an oracle upper bound used to separate an "
                             "activation-range mismatch from a genuine INT8 precision "
                             "limit; it is never deployable and is labelled as such")
    parser.add_argument("--random-repeats", type=int, default=1,
                        help="independent random subsamples per (size, method, format)")
    parser.add_argument("--seed", type=int, default=None,
                        help="seed for the random calibration draws (default: the "
                             f"calibration_seed recorded in report.json, else "
                             f"{DEFAULT_CALIBRATION_SEED})")
    parser.add_argument("--allow-mismatch", action="store_true",
                        help="DEBUG ONLY: downgrade the pairing failure to a warning "
                             "(results would be leak-contaminated; never publish them)")
    return parser.parse_args()


# --------------------------------------------------------------------------- cache
def load_cache(data_dir: Path) -> dict:
    train_meta = np.load(data_dir / "windows-train-meta.npz", allow_pickle=True)
    test_meta = np.load(data_dir / "windows-test-meta.npz", allow_pickle=True)
    stats_path = data_dir / "windows-stats.json"
    return {
        "train_x": np.load(data_dir / "windows-train.npy", mmap_mode="r"),
        "test_x": np.load(data_dir / "windows-test.npy", mmap_mode="r"),
        "train_y": np.asarray(train_meta["y"]),
        "train_subject": np.asarray(train_meta["subject"]),
        "train_record": np.asarray(train_meta["record"]) if "record" in train_meta
        else np.asarray(train_meta["subject"]),
        "test_y": np.asarray(test_meta["y"]),
        "test_subject": np.asarray(test_meta["subject"]),
        "test_record": np.asarray(test_meta["record"]) if "record" in test_meta
        else np.asarray(test_meta["subject"]),
        "stats": json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {},
    }


# --------------------------------------------------------------------------- pairing
def _fmt(missing: list[str], extra: list[str]) -> str:
    parts = []
    if missing:
        parts.append(f"missing from cache (fold test subjects not cached): {missing[:8]}"
                     + (" ..." if len(missing) > 8 else ""))
    if extra:
        parts.append(f"unexpected in cache (not fold test subjects): {extra[:8]}"
                     + (" ..." if len(extra) > 8 else ""))
    return "; ".join(parts)


def verify_pairing(cache: dict, fold: int, splits: dict[str, int],
                   splits_path: Path, report: dict | None,
                   allow_mismatch: bool = False) -> dict:
    """Refuse to score unless the cached test windows are fold ``fold``'s own."""
    problems: list[str] = []
    fold_test = {subject for subject, assigned in splits.items() if int(assigned) == fold}
    cached_test = set(str(v) for v in cache["test_subject"])
    cached_train = set(str(v) for v in cache["train_subject"])
    fingerprint = splits_fingerprint(splits)

    missing = sorted(fold_test - cached_test)
    extra = sorted(cached_test - fold_test)
    print(f"[pairing] splits.csv            = {splits_path} ({len(splits)} subjects)")
    print(f"[pairing] splits fingerprint    = {fingerprint}")
    print(f"[pairing] fold {fold} test subjects = {len(fold_test)}")
    print(f"[pairing] cached test subjects  = {len(cached_test)} "
          f"({len(cache['test_y'])} windows)")
    print(f"[pairing] cached train subjects = {len(cached_train)} "
          f"({len(cache['train_y'])} windows)")
    if missing or extra:
        problems.append("cached test subjects are not fold "
                        f"{fold}'s test subjects: " + _fmt(missing, extra))
    else:
        print(f"[pairing] PASS cached test subjects == fold {fold} test subjects "
              f"({len(fold_test)}/{len(fold_test)})")

    overlap = cached_train & cached_test
    if overlap:
        problems.append(f"{len(overlap)} subjects appear in both the cached train and "
                        f"the cached test split, e.g. {sorted(overlap)[:8]}")
    elif missing or extra:
        print("[pairing] NOTE cached train/test subjects are disjoint, but they belong "
              "to a different fold, so that check proves nothing here")
    else:
        print("[pairing] PASS cached train and cached test subjects are disjoint")

    if fingerprint != EXPECTED_CIROR_FINGERPRINT:
        problems.append(f"splits.csv fingerprint {fingerprint} does not match the "
                        f"frozen CirCor fingerprint {EXPECTED_CIROR_FINGERPRINT}")

    report_info: dict = {}
    if report is not None:
        source = report.get("folds_source", {})
        report_info = {
            "report_fold": source.get("fold"),
            "report_fingerprint": source.get("fingerprint"),
            "report_windows_test": report.get("windows", {}).get("test"),
            "report_test_subjects": source.get("test_subjects"),
        }
        if source.get("fold") != fold:
            problems.append(f"report.json folds_source.fold={source.get('fold')} "
                            f"!= --fold {fold}")
        if source.get("fingerprint") != fingerprint:
            problems.append(f"report.json fingerprint {source.get('fingerprint')} "
                            f"!= splits.csv fingerprint {fingerprint}")
        if report.get("windows", {}).get("test") != int(len(cache["test_y"])):
            problems.append(f"report.json windows.test={report.get('windows', {}).get('test')} "
                            f"!= cached test windows {len(cache['test_y'])}")
        if source.get("test_subjects") != len(fold_test):
            problems.append(f"report.json test_subjects={source.get('test_subjects')} "
                            f"!= fold {fold} test subjects {len(fold_test)}")
        if not problems:
            print(f"[pairing] PASS report.json folds_source.fold="
                  f"{source.get('fold')} and fingerprint match; "
                  f"windows.test={report.get('windows', {}).get('test')}")

    if problems:
        headline = ("[pairing] PAIRING WARNING (continuing because --allow-mismatch):"
                    if allow_mismatch else "[pairing] REFUSING TO RUN:")
        message = headline + "\n  - " + "\n  - ".join(problems)
        if not allow_mismatch:
            raise SystemExit(message)
        print(message + "\n[pairing] --allow-mismatch set: results are LEAKY, do not publish",
              file=sys.stderr)

    return {
        "fold": fold,
        "splits_file": str(splits_path),
        "splits_fingerprint": fingerprint,
        "fold_test_subjects": len(fold_test),
        "cached_test_subjects": len(cached_test),
        "cached_test_windows": int(len(cache["test_y"])),
        "cached_train_subjects": len(cached_train),
        "cached_train_windows": int(len(cache["train_y"])),
        "test_subjects_identical": not (missing or extra),
        "train_test_subjects_disjoint": not overlap,
        **report_info,
    }


# --------------------------------------------------------------------------- scoring
def model_size_bytes(path: Path) -> dict:
    """Size of an ONNX model *and* its external-data sidecar.

    ``train_cnn.export_onnx`` writes weights to ``model-fp32.onnx.data`` (162 KiB)
    and keeps only the 37 KiB graph in the ``.onnx`` file, so reporting the bare
    ``.onnx`` size would claim INT8 (69 KiB, self-contained) is 1.9x *larger*
    than FP32 because FP32's graphs are bigger. Both files are one model.
    """
    onnx_bytes = path.stat().st_size
    tracked: list[str] = []
    try:
        import onnx

        model = onnx.load(str(path), load_external_data=False)
        for initializer in model.graph.initializer:
            for entry in initializer.external_data:
                if entry.key == "location":
                    tracked.append(entry.value)
    except Exception:  # noqa: BLE001 - fall back to the conventional sidecar name
        pass
    candidates = [path.parent / name for name in dict.fromkeys(tracked)]
    candidates.append(path.parent / f"{path.name}.data")
    sidecars, seen = [], set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and candidate != path:
            sidecars.append(candidate)
    external = sum(candidate.stat().st_size for candidate in sidecars)
    return {
        "onnx_bytes": int(onnx_bytes),
        "external_data_bytes": int(external),
        "total_bytes": int(onnx_bytes + external),
        "external_data_files": [candidate.name for candidate in sidecars],
    }


def make_session(path: Path):
    import onnxruntime as ort

    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def predict(path: Path, windows) -> np.ndarray:
    """Per-window murmur probability.

    The exported graph ends in a ``Gemm`` named ``logit`` and ``train_cnn``
    applies the sigmoid in Python before patient aggregation. Averaging logits
    would rank patients differently (mean-of-logits != mean-of-probabilities),
    so the sigmoid is applied here too.
    """
    session = make_session(path)
    name = session.get_inputs()[0].name
    out = []
    for start in range(0, len(windows), 128):
        batch = np.asarray(windows[start:start + 128], dtype=np.float32)
        out.append(session.run(None, {name: batch})[0])
    logits = np.concatenate(out).reshape(-1) if out else np.zeros(0)
    return 1.0 / (1.0 + np.exp(-logits.astype(np.float64)))


def patient_auroc(probabilities: np.ndarray, labels: np.ndarray,
                  subjects: np.ndarray) -> tuple[float, int]:
    grouped: dict[str, list[float]] = {}
    for subject, probability in zip(subjects, probabilities):
        grouped.setdefault(str(subject), []).append(float(probability))
    ids = sorted(grouped)
    scores = np.asarray([float(np.mean(grouped[key])) for key in ids])
    truth = np.asarray([int(labels[str(s) == np.asarray(subjects)][0]) for s in ids],
                      dtype=int)
    positives = int((truth == 1).sum())
    negatives = int((truth == 0).sum())
    if positives == 0 or negatives == 0:
        return float("nan"), len(ids)
    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    ranks = np.empty(len(scores), dtype=float)
    start = 0
    while start < len(sorted_scores):
        end = start + 1
        while end < len(sorted_scores) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0 + 1.0
        start = end
    auroc = (ranks[truth == 1].sum() - positives * (positives + 1) / 2.0) / (
        positives * negatives)
    return float(auroc), len(ids)


def calibration_index(size: int, mode: str, length: int, seed: int,
                      repeat: int, fold: int = 0) -> np.ndarray:
    """Indices of the calibration windows, reproducible from ``(seed, fold, size, repeat)``.

    This is the single implementation shared by ``train_cnn.py`` (the published
    measurement) and this diagnostic, so the sampled calibration set in a report
    can be reproduced exactly. Keeping it here also keeps it unit-testable
    without torch.

    ``mode="random"`` is a uniform subsample without replacement. The fold is
    part of the seed so two folds do not silently share one draw; ``repeat``
    gives independent draws of the same recipe for a spread estimate.
    """
    if mode == "linspace":
        if size > length:
            return np.arange(length)
        return np.unique(np.linspace(0, length - 1, size).round().astype(int))
    if mode == "random":
        rng = np.random.default_rng(
            np.random.SeedSequence([int(seed), int(fold), int(size), int(repeat)]))
        return np.sort(rng.choice(length, size=min(size, length), replace=False))
    raise SystemExit(f"unknown calibration mode {mode!r}")


def published_config(settings: dict | None) -> str:
    """The sweep's config name for the recipe recorded in a report.

    ``train_cnn.py`` writes ``quantization.settings``; a report without it was
    produced by the pre-2026-10-19 recipe (QOperator, QUInt8 activations, MinMax,
    200 linspace windows), which is what the fallback names.
    """
    if not settings:
        return "qoperator/quint8/minmax/n200/linspace"
    fmt = str(settings.get("format", "QOperator")).lower()
    activation = str(settings.get("activation_type", "quint8"))
    method = str(settings.get("calibrate_method", "minmax"))
    size = int(settings.get("calibration_size", DEFAULT_CALIBRATION_SIZE))
    sampling = str(settings.get("calibration_sampling", "linspace"))
    name = f"{fmt}/{activation}/{method}/n{size}/{sampling}"
    percentile = settings.get("calibrate_percentile")
    if method == "percentile" and percentile is not None:
        name += f"@{float(percentile):g}"
    return name


def coverage(index: np.ndarray, subjects: np.ndarray, records: np.ndarray,
             labels: np.ndarray) -> dict:
    """How much of the *distribution* the calibration sample actually spans."""
    unique_subjects = np.unique(subjects[index])
    unique_records = np.unique(records[index])
    return {
        "windows": int(len(index)),
        "unique_subjects": int(len(unique_subjects)),
        "unique_records": int(len(unique_records)),
        "positive_fraction": float(np.mean(labels[index])) if len(index) else float("nan"),
    }


def quantize(source: Path, target: Path, calibration, method: str, fmt: str,
             percentile: float, granularity: str, activation: str,
             input_name: str) -> None:
    from onnxruntime.quantization import (
        CalibrationDataReader,
        CalibrationMethod,
        QuantFormat,
        QuantType,
        quantize_static,
    )

    class Reader(CalibrationDataReader):
        def __init__(self, windows) -> None:
            self._iterator = iter(windows)

        def get_next(self):
            try:
                window = next(self._iterator)
            except StopIteration:
                return None
            return {input_name: np.asarray(window, dtype=np.float32)[None, :]}

        def rewind(self) -> None:
            self._iterator = iter(calibration)

    calibrate = {
        "minmax": CalibrationMethod.MinMax,
        "percentile": CalibrationMethod.Percentile,
        "entropy": CalibrationMethod.Entropy,
    }[method]
    quant_format = {"qoperator": QuantFormat.QOperator, "qdq": QuantFormat.QDQ}[fmt]
    activation_type = {"quint8": QuantType.QUInt8, "int8": QuantType.QInt8}[activation]
    # Only the percentile arm needs extra_options. Leaving MinMax on the ORT
    # defaults is what makes the "qoperator/minmax/n200/linspace/quint8" row
    # byte-comparable with hs_cnn.quantize_onnx_static.
    extra = {"CalibPercentile": float(percentile)} if method == "percentile" else None
    quantize_static(
        str(source),
        str(target),
        Reader(calibration),
        quant_format=quant_format,
        per_channel=granularity == "per_channel",
        activation_type=activation_type,
        weight_type=QuantType.QInt8,
        calibrate_method=calibrate,
        extra_options=extra,
    )


# --------------------------------------------------------------------------- main
def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    source = Path(args.onnx)
    splits_path = Path(args.splits_file) if args.splits_file else data_dir / "splits.csv"
    if not source.exists():
        raise SystemExit(f"missing ONNX model: {source}")
    if not splits_path.exists():
        raise SystemExit(f"missing frozen splits file: {splits_path}")

    report_path = source.parent / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    if args.fold is None:
        if report is None:
            raise SystemExit("--fold is required when no sibling report.json exists")
        args.fold = int(report["folds_source"]["fold"])
        print(f"[pairing] fold {args.fold} inferred from {report_path}")

    settings = (report or {}).get("quantization", {}).get("settings") or {}
    report_seed = settings.get("calibration_seed")
    seed = int(args.seed) if args.seed is not None else int(
        report_seed if report_seed is not None else DEFAULT_CALIBRATION_SEED)
    if report_seed is not None and int(report_seed) != seed:
        print(f"[sweep] note: report.json used calibration_seed={report_seed}, "
              f"sweep uses {seed}; the 'random' rows will not reproduce that report")
    cache = load_cache(data_dir)
    splits = load_splits(splits_path)
    pairing = verify_pairing(cache, int(args.fold), splits, splits_path, report,
                             allow_mismatch=bool(args.allow_mismatch))

    test_x, test_y, test_subject = cache["test_x"], cache["test_y"], cache["test_subject"]
    if args.calib_source == "test":
        print("[oracle] WARNING calibration windows are taken from the fold's TEST "
              "windows; these numbers are an upper bound, not a deployment result",
              file=sys.stderr)
        pool_x, pool_subject = cache["test_x"], cache["test_subject"]
        pool_record = cache["test_record"] if "test_record" in cache else cache["test_subject"]
        pool_y = cache["test_y"]
    else:
        pool_x, pool_subject = cache["train_x"], cache["train_subject"]
        pool_record, pool_y = cache["train_record"], cache["train_y"]
    input_length = int(np.asarray(test_x[:1]).shape[1])
    session = make_session(source)
    if list(session.get_inputs()[0].shape)[1] != input_length:
        raise SystemExit(f"ONNX input length {session.get_inputs()[0].shape} != cached "
                         f"window length {input_length}")
    input_name = session.get_inputs()[0].name

    reference = predict(source, test_x)
    fp32, n_patients = patient_auroc(reference, test_y, test_subject)
    fp32_size = model_size_bytes(source)
    print(f"[fp32] patient AUROC = {fp32:.6f} over {n_patients} patients "
          f"({len(test_y)} test windows)")
    print(f"[fp32] size = {fp32_size['total_bytes']} B "
          f"(onnx {fp32_size['onnx_bytes']} + external {fp32_size['external_data_bytes']})")

    reference_report = {}
    if report is not None:
        quant = report.get("quantization", {})
        reported_fp32 = quant.get("fp32_test_patient", {}).get("auroc")
        reported_int8 = quant.get("int8_test_patient", {}).get("auroc")
        reference_report = {
            "report_fp32_patient_auroc": reported_fp32,
            "report_int8_patient_auroc": reported_int8,
            "report_int8_onnx_bytes": quant.get("int8_onnx_bytes"),
            "recomputed_minus_report_fp32": (
                None if reported_fp32 is None else fp32 - float(reported_fp32)),
        }
        if reported_fp32 is not None:
            delta = abs(fp32 - float(reported_fp32))
            if delta > 1e-4:
                headline = ("[pairing] PAIRING WARNING (--allow-mismatch):"
                            if args.allow_mismatch else "[pairing] REFUSING TO RUN:")
                message = (f"{headline} recomputed FP32 patient AUROC "
                           f"{fp32:.6f} != report.json {float(reported_fp32):.6f} "
                           f"(|delta|={delta:.2e}); this ONNX model and this window "
                           f"cache are not the same fold")
                if not args.allow_mismatch:
                    raise SystemExit(message)
                print(message + "\n[pairing] --allow-mismatch set: results are LEAKY",
                      file=sys.stderr)
            else:
                print(f"[pairing] PASS FP32 patient AUROC reproduces report.json "
                      f"(|delta|={delta:.2e})")

    results = [{
        "config": "fp32",
        "format": "fp32",
        "method": "none",
        "calib_size": 0,
        "sampling": "none",
        "patient_auroc": fp32,
        "drop": 0.0,
        **fp32_size,
    }]

    workdir = Path(tempfile.mkdtemp(prefix="requantize-"))
    started = time.time()
    try:
        for size in (int(value) for value in args.calib_sizes.split(",") if value.strip()):
            if size > len(pool_x):
                print(f"[skip] calib size {size} > {len(pool_x)} "
                      f"{args.calib_source} windows")
                continue
            for mode in (value.strip() for value in args.modes.split(",") if value.strip()):
                repeats = args.random_repeats if mode == "random" else 1
                for method in (v.strip() for v in args.methods.split(",") if v.strip()):
                    for fmt in (v.strip() for v in args.formats.split(",") if v.strip()):
                        for activation in (v.strip() for v in args.activation_types.split(",")
                                           if v.strip()):
                            runs, coverages, size_info, error = [], [], None, None
                            for repeat in range(repeats):
                                index = calibration_index(size, mode, len(pool_x),
                                                          seed, repeat,
                                                          fold=int(args.fold))
                                coverage_info = coverage(index, pool_subject, pool_record,
                                                        pool_y)
                                calibration = np.asarray(pool_x[index], dtype=np.float32)
                                target = workdir / (
                                    f"int8-{size}-{mode}-{method}-{fmt}-{activation}-"
                                    f"{repeat}.onnx")
                                try:
                                    quantize(source, target, calibration, method, fmt,
                                             args.percentile, args.weight_granularity,
                                             activation, input_name)
                                    probabilities = predict(target, test_x)
                                    auroc, _ = patient_auroc(probabilities, test_y,
                                                             test_subject)
                                    runs.append(auroc)
                                    coverages.append(coverage_info)
                                    size_info = model_size_bytes(target)
                                except Exception as failure:  # noqa: BLE001 - one bad config must not stop the sweep
                                    error = f"{type(failure).__name__}: {failure}"
                                finally:
                                    for leftover in workdir.glob(f"{target.name}*"):
                                        leftover.unlink(missing_ok=True)
                                    target.unlink(missing_ok=True)
                            name = f"{fmt}/{activation}/{method}/n{size}/{mode}"
                            if method == "percentile":
                                name += f"@{args.percentile:g}"
                            if error is not None and not runs:
                                entry = {"config": name, "error": error}
                                print(f"  {name:<44} ERROR {error[:70]}")
                            else:
                                mean_auroc = float(np.mean(runs))
                                entry = {
                                    "config": name,
                                    "format": fmt,
                                    "activation_type": activation,
                                    "method": method,
                                    "calib_size": int(size),
                                    "sampling": mode,
                                    "calibration_source": args.calib_source,
                                    "percentile": (args.percentile
                                                   if method == "percentile" else None),
                                    "weight_granularity": args.weight_granularity,
                                    "patient_auroc": mean_auroc,
                                    "patient_auroc_runs": [float(value) for value in runs],
                                    "drop": fp32 - mean_auroc,
                                    "calibration_coverage": coverages[0],
                                    **size_info,
                                }
                                if error is not None:
                                    entry["partial_error"] = error
                                print(f"  {name:<44} AUROC {mean_auroc:.4f}  "
                                      f"drop {fp32 - mean_auroc:+.4f}  "
                                      f"{size_info['total_bytes']:>7} B  "
                                      f"calib subj/rec {coverages[0]['unique_subjects']:>3}/"
                                      f"{coverages[0]['unique_records']:<4}")
                            results.append(entry)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    elapsed = time.time() - started
    # The row must reproduce report.json's INT8 AUROC and file size, otherwise
    # the sweep is measuring a different quantizer than the report did. The
    # expected configuration comes from the report itself when it records the
    # recipe (quantization.settings, written since 2026-10-19); older reports
    # fall back to the QUInt8/MinMax/200-linspace recipe they were built with.
    baseline_parity: dict = {}
    expected_config = published_config(settings)
    baseline = next((row for row in results
                     if str(row.get("config", "")) == expected_config), None)
    if baseline is None:
        print(f"[baseline] note: the report's recipe `{expected_config}` is not in "
              f"this grid, so parity with report.json was not checked")
    if baseline and reference_report.get("report_int8_patient_auroc") is not None:
        delta = baseline["patient_auroc"] - float(
            reference_report["report_int8_patient_auroc"])
        size_delta = None
        if reference_report.get("report_int8_onnx_bytes") is not None:
            size_delta = baseline["total_bytes"] - int(
                reference_report["report_int8_onnx_bytes"])
        baseline_parity = {
            "config": baseline["config"],
            "recomputed_patient_auroc": baseline["patient_auroc"],
            "report_patient_auroc": reference_report["report_int8_patient_auroc"],
            "patient_auroc_delta": delta,
            "bytes_delta": size_delta,
        }
        verdict = "PASS" if abs(delta) <= 1e-4 and size_delta in (None, 0) else "MISMATCH"
        print(f"[baseline] {verdict} published INT8 row: recomputed "
              f"{baseline['patient_auroc']:.6f} vs report "
              f"{float(reference_report['report_int8_patient_auroc']):.6f} "
              f"(|delta|={abs(delta):.2e}), bytes delta {size_delta}")
        if verdict == "MISMATCH" and not args.allow_mismatch:
            raise SystemExit("[baseline] REFUSING TO WRITE: the sweep does not "
                             "reproduce the published INT8 configuration")

    payload = {
        "tool": "tools/ai/requantize.py",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "onnx": str(source),
        "data_dir": str(data_dir),
        "fold": int(args.fold),
        "seed": int(seed),
        "percentile": float(args.percentile),
        "weight_granularity": args.weight_granularity,
        "activation_types": [v.strip() for v in args.activation_types.split(",") if v.strip()],
        "random_repeats": int(args.random_repeats),
        "calibration_source": args.calib_source,
        "runtime_seconds": round(elapsed, 1),
        "pairing": pairing,
        "fp32": {"patient_auroc": fp32, "patients": n_patients, **fp32_size},
        "report_reference": reference_report,
        "baseline_parity": baseline_parity,
        "results": results,
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"[wrote] {out}")


if __name__ == "__main__":
    main()
