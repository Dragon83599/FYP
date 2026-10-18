#!/usr/bin/env python3
"""Does a *larger* model buy accuracy on CirCor, or is the data the bottleneck?

The team is considering replacing the ESP32-S3 host with a Raspberry Pi because
"more compute would allow a stronger model".  That trade costs roughly 10-20x
the battery (1-2 h vs ~20 h), a 20-40 s boot, and 5-15x the unit cost, so it is
only justified if capacity actually buys patient-level accuracy on *this* data
(874 patients / 179 positives / 11,728 annotated windows).

This script is the pre-registered experiment that decides it:

1. Three arms are trained on the **same frozen patient folds** (the sha256 of
   ``data/processed/circor/splits.csv`` is recorded), with the **same annotated-
   window protocol**, the **same hyper-parameters** (30 epochs, patience 6, batch
   64, lr 1e-3, weight decay 1e-4) and the same seeds.  Only the architecture
   changes: ``small`` (the 41,105-parameter baseline), ``resnet`` and ``tcn``.
2. Every arm reports patient-level AUROC / balanced accuracy per fold and
   pooled, a patient-bootstrap 95% CI, window AUROC, parameters, MACs/window and
   training wall time.
3. The pre-registered decision rule (fixed before any large arm was run, and
   repeated verbatim in the report) is:

   * the large-model arm wins only if its pooled patient AUROC exceeds the
     ``small`` arm's by **more than the pooled 95% CI half-width of the small
     arm**, i.e. the improvement cannot be explained by patient resampling
     noise;
   * otherwise the difference is within noise and the conclusion is that
     compute is *not* the bottleneck, so a stronger host would not buy accuracy.

Nothing is ever selected on a test fold: the decision threshold of every fold
comes from that fold's validation split (``hs_metrics.best_threshold``), the
early-stopping checkpoint is chosen on the validation split, and the test folds
are read exactly once, at the end, for reporting.

Why the architecture table lives in this module
-----------------------------------------------
CI installs **NumPy only** (``.github/workflows/ci.yml``), so ``tests/python``
must stay importable without PyTorch.  The immutable architecture table plus the
analytic parameter/MAC formulas are therefore pure Python here; ``hs_cnn``
builds the ``torch`` modules *from the same table*, so the budget numbers in the
report cannot drift from the models that were trained.  PyTorch is imported
lazily, inside the functions that need it.

Usage (see ``--help`` for everything)::

    OMP_NUM_THREADS=4 .venv/bin/python tools/ai/large_model_experiment.py \\
        --models small resnet tcn \\
        --out-dir data/reports/circor-large-model

The default ``--models`` is ``small resnet tcn``: the ``small`` arm is the
control that proves this driver reproduces the published baseline
(``data/reports/circor-cv-cnn-annotated``) on the same machine and thread count,
so an honest comparison needs no cross-machine assumption.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Architecture table and budget formulas (pure Python: importable without torch)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConvStageSpec:
    """One stage of the convolutional trunk.

    ``blocks`` blocks are applied in series.  The first block sees
    ``in_channels`` and applies ``stride``; the remaining blocks see
    ``channels`` and stride 1.  ``residual`` selects a residual block (two
    convolutions plus a 1x1 shortcut when the shape changes) instead of a plain
    convolution+BN block (the ``ConvBlock`` of the small CNN).  ``pool`` is a
    max-pool applied once after the stage.  ``dilations`` gives the dilation of
    each block (empty means every block is dilation 1).
    """

    blocks: int
    in_channels: int
    channels: int
    kernel: int
    stride: int = 1
    residual: bool = False
    pool: int = 1
    dilations: tuple[int, ...] = ()


@dataclass(frozen=True)
class ConvNetSpec:
    """A 1D stem + stages + global-average-pool classifier.

    The stem is ``conv(1 -> stem_channels, k=stem_kernel, s=stem_stride)`` +
    BatchNorm + ReLU + max-pool(``stem_pool``).  The classifier is a single
    linear layer after global average pooling, exactly like ``Small1DCNN``.
    Every input window is standardised over time (mean 0 / std 1) before the
    first convolution, matching ``Small1DCNN.forward``; that is the shared
    preprocessing, not an architecture choice.
    """

    name: str
    description: str
    stem_channels: int
    stem_kernel: int
    stem_stride: int
    stem_pool: int
    stages: tuple[ConvStageSpec, ...]
    head_dropout: float = 0.2
    role: str = "candidate"


#: The published baseline: ``hs_cnn.Small1DCNN`` expressed in this table.
#:
#: ``ConvBlock(1, 16, k=15, s=4, pool=2)`` is the stem, the three remaining
#: ``ConvBlock``s are the stages.  ``tests/python/test_large_model.py`` pins
#: ``architecture_parameters("small") == 41,105`` and
#: ``architecture_macs("small") ==`` the value ``hs_cnn.measure_macs`` returns,
#: so the table cannot silently describe a different model than the one that is
#: trained.
SMALL_SPEC = ConvNetSpec(
    name="small",
    description="Small1DCNN baseline (4 conv blocks, 41,105 params)",
    stem_channels=16,
    stem_kernel=15,
    stem_stride=4,
    stem_pool=2,
    stages=(
        ConvStageSpec(blocks=1, in_channels=16, channels=32, kernel=11, stride=2, pool=2),
        ConvStageSpec(blocks=1, in_channels=32, channels=64, kernel=7, stride=2, pool=2),
        ConvStageSpec(blocks=1, in_channels=64, channels=64, kernel=5, stride=2, pool=2),
    ),
    role="baseline",
)

#: Candidate A - 1D ResNet.  The front end (stem + the baseline's second and
#: third ``ConvBlock``s) is *identical* to ``SMALL_SPEC`` and brings the signal to
#: the 62-frame map (31 Hz over the 2 s window) that the baseline's last
#: ``ConvBlock`` consumes; the baseline's single 64-channel block there is
#: replaced by 8 residual blocks of 96 channels, which is where all of the extra
#: capacity lives (the baseline's own last block is what the trunk replaces).
#: Keeping the shared front end means a per-arm difference cannot come from a
#: different downsampling/aliasing path - only capacity differs.
RESNET_SPEC = ConvNetSpec(
    name="resnet",
    description="1D ResNet: baseline front end + 8 x 96-channel residual blocks",
    stem_channels=16,
    stem_kernel=15,
    stem_stride=4,
    stem_pool=2,
    stages=(
        ConvStageSpec(blocks=1, in_channels=16, channels=32, kernel=11, stride=2, pool=2),
        ConvStageSpec(blocks=1, in_channels=32, channels=64, kernel=7, stride=2, pool=2),
        ConvStageSpec(blocks=8, in_channels=64, channels=96, kernel=3, residual=True),
    ),
    role="large",
)

#: Candidate B - dilated TCN.  Same front end again, then 8 residual blocks of
#: 80 channels with growing dilation at a single resolution: no further
#: downsampling, so every block sees the full 62-frame map and the receptive
#: field covers the whole 2 s window (dilations 1..16 twice over span 153
#: frames = 4.9 s).  Same cost class as the ResNet, different inductive bias.
TCN_SPEC = ConvNetSpec(
    name="tcn",
    description="Dilated TCN: baseline front end + 8 x 80-channel dilated residual blocks",
    stem_channels=16,
    stem_kernel=15,
    stem_stride=4,
    stem_pool=2,
    stages=(
        ConvStageSpec(blocks=1, in_channels=16, channels=32, kernel=11, stride=2, pool=2),
        ConvStageSpec(blocks=1, in_channels=32, channels=64, kernel=7, stride=2, pool=2),
        ConvStageSpec(
            blocks=8,
            in_channels=64,
            channels=80,
            kernel=3,
            residual=True,
            dilations=(1, 2, 4, 8, 16, 1, 2, 4),
        ),
    ),
    role="large",
)

#: Name -> spec.  ``hs_cnn.build_model`` and ``cross_validate.py --model`` use
#: these keys.
ARCHITECTURE_SPECS: dict[str, ConvNetSpec] = {
    spec.name: spec
    for spec in (SMALL_SPEC, RESNET_SPEC, TCN_SPEC)
}

#: Architectures that need PyTorch (``logreg`` is NumPy-only and is not part of
#: this experiment; ``small`` is also the historical ``cnn`` name).
CONV_ARCHITECTURES: tuple[str, ...] = tuple(ARCHITECTURE_SPECS)

#: ``cross_validate.py --model`` names that mean "the small CNN baseline".
SMALL_ALIASES: tuple[str, ...] = ("cnn", "small")

#: Input window length in samples (2 s at 4 kHz), fixed by the freeze protocol.
WINDOW_SAMPLES = 8000


def conv_out_length(length: int, kernel: int, stride: int = 1) -> int:
    """Output length of a same-padded convolution (odd kernel).

    ``padding = kernel // 2`` for every even/odd kernel here, so an odd kernel
    keeps ``length`` at stride 1 and ``(length - 1) // stride + 1`` otherwise.
    Even kernels are not used by any spec in this module.
    """
    padding = kernel // 2
    return (length + 2 * padding - kernel) // stride + 1


def _stage_block_shape(stage: ConvStageSpec, index: int) -> tuple[int, int]:
    """``(in_channels, stride)`` of block ``index`` inside ``stage``."""
    if index == 0:
        return stage.in_channels, stage.stride
    return stage.channels, 1


def parameters_of(spec: ConvNetSpec) -> int:
    """Analytic parameter count of ``spec``: every trainable tensor.

    Counted exactly like ``hs_cnn.count_parameters``: convolutions have no bias,
    every BatchNorm contributes ``2 * channels`` (weight + bias), the final
    linear layer contributes ``in_features + 1``.
    """
    total = spec.stem_channels * 1 * spec.stem_kernel + 2 * spec.stem_channels
    for stage in spec.stages:
        for index in range(stage.blocks):
            in_channels, stride = _stage_block_shape(stage, index)
            total += in_channels * stage.channels * stage.kernel
            total += 2 * stage.channels
            if stage.residual:
                total += stage.channels * stage.channels * stage.kernel
                total += 2 * stage.channels
                if stride != 1 or in_channels != stage.channels:
                    total += stage.channels * in_channels
                    total += 2 * stage.channels
    total += spec.stages[-1].channels + 1
    return int(total)


def macs_of(spec: ConvNetSpec, input_length: int = WINDOW_SAMPLES) -> int:
    """Analytic multiply-accumulate count of ``spec`` for one window.

    Same convention as ``hs_cnn.measure_macs``: one MAC per (output element x
    input channel x kernel tap) for a ``Conv1d`` and per weight for a
    ``Linear``; BatchNorm, ReLU, max-pool and global average pooling are
    reported as zero.  The test suite pins the two implementations against each
    other for every architecture.
    """
    length = int(input_length)
    stem_out = conv_out_length(length, spec.stem_kernel, spec.stem_stride)
    total = stem_out * spec.stem_channels * 1 * spec.stem_kernel
    length = stem_out // spec.stem_pool
    for stage in spec.stages:
        for index in range(stage.blocks):
            in_channels, stride = _stage_block_shape(stage, index)
            out = conv_out_length(length, stage.kernel, stride)
            total += out * stage.channels * in_channels * stage.kernel
            if stage.residual:
                total += out * stage.channels * stage.channels * stage.kernel
                if stride != 1 or in_channels != stage.channels:
                    total += out * stage.channels * in_channels
            length = out
        length = length // stage.pool
    total += spec.stages[-1].channels
    return int(total)


def architecture_parameters(name: str) -> int:
    """Analytic parameter count of the architecture called ``name``."""
    return parameters_of(ARCHITECTURE_SPECS[name])


def architecture_macs(name: str, input_length: int = WINDOW_SAMPLES) -> int:
    """Analytic MAC/window count of the architecture called ``name``."""
    return macs_of(ARCHITECTURE_SPECS[name], input_length)


def architecture_budget(name: str, input_length: int = WINDOW_SAMPLES) -> dict:
    """The budget row that goes into the report for one architecture."""
    spec = ARCHITECTURE_SPECS[name]
    parameters = architecture_parameters(name)
    macs = architecture_macs(name, input_length)
    return {
        "name": name,
        "description": spec.description,
        "role": spec.role,
        "parameters": parameters,
        "macs_per_window": macs,
        "int8_weight_bytes_estimate": parameters,
        "fp32_weight_bytes": 4 * parameters,
        # 5% of 2026-era flash budget is not the point here; the comparison is
        # relative to the 41,105-parameter baseline.
        "parameter_ratio_to_small": parameters / architecture_parameters("small"),
        "macs_ratio_to_small": macs / architecture_macs("small"),
    }


def budget_table(input_length: int = WINDOW_SAMPLES) -> list[dict]:
    return [architecture_budget(name, input_length) for name in CONV_ARCHITECTURES]


#: The pre-registered decision rule.  Written down (and committed in this file)
#: *before* any large-model arm was trained, so it cannot be adjusted to the
#: result afterwards.
DECISION_RULE = (
    "A larger model is a real compute ceiling only if its pooled patient AUROC "
    "(over all folds' test patients, each patient scored exactly once) exceeds "
    "the small CNN control arm's pooled patient AUROC by more than the control "
    "arm's pooled patient-AUROC 95% bootstrap half-width.  Otherwise the "
    "difference is within patient-resampling noise and compute is not the "
    "bottleneck at 874 patients / 179 positives.  The rule is one-sided (a "
    "larger model can only justify a stronger host by winning), the arm with "
    "the best pooled patient AUROC is compared, and no architecture is "
    "selected, tuned or checkpoint-selected on a test fold."
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", default="data/processed/circor")
    parser.add_argument("--out-dir", default="data/reports/circor-large-model")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["small", "resnet", "tcn"],
        help=(
            "Arms to run, in order. 'small' is the control that must reproduce "
            "the published baseline; 'resnet'/'tcn' are the capacity arms."
        ),
    )
    parser.add_argument(
        "--splits-file",
        default="data/processed/circor/splits.csv",
        help=(
            "Frozen patient folds (the protocol artifact). Default is the file "
            "the published baseline used; its sha256 is recorded in the report."
        ),
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--window-sec", type=float, default=2.0)
    parser.add_argument("--hop-sec", type=float, default=1.0)
    parser.add_argument("--max-windows-per-record", type=int, default=4)
    parser.add_argument("--window-selection", choices=("all", "annotated"), default="annotated")
    parser.add_argument("--min-annotated-fraction", type=float, default=0.5)
    parser.add_argument("--silence-rms", type=float, default=1e-4)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument(
        "--seed",
        type=int,
        default=20261008,
        help=(
            "Seed for the train/val carve (effective seed = seed + fold, exactly "
            "like cross_validate.py) and for torch/numpy. cross_validate.py's "
            "default is 20261008, and the published baseline report records "
            "20261008 as its carve seed, so every arm here uses it too: changing "
            "it would move patients between train and validation and break the "
            "comparison."
        ),
    )
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument(
        "--baseline-report",
        default="data/reports/circor-cv-cnn-annotated/report.json",
        help="Published baseline report the 'small' control arm is checked against.",
    )
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument(
        "--only-folds",
        default="",
        help=(
            "Comma-separated fold indices, for smoke-testing the plumbing. A run "
            "that does not evaluate every frozen fold is not a result: the "
            "decision rule refuses to fire and the report says so."
        ),
    )
    parser.add_argument(
        "--windows-cache",
        default="",
        help=(
            "Optional directory for the collected windows. Re-running an arm "
            "then skips wav reading and band-pass filtering (several minutes). "
            "The cache stores exactly what was collected for this protocol."
        ),
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help=(
            "Rebuild the comparison report from the per-arm report.json and "
            "patient-probabilities.csv already under --out-dir, without "
            "training. The numbers are recomputed from the saved test "
            "probabilities, so this re-renders the report with the current code "
            "and cannot change a result."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def settings_from_args(args: argparse.Namespace):
    """The training hyper-parameters, the same shape ``cross_validate.cnn_args`` gives."""
    return types.SimpleNamespace(
        epochs=args.epochs,
        patience=args.patience,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        seed=args.seed,
    )


def collection_args(args: argparse.Namespace) -> types.SimpleNamespace:
    """The subset of arguments ``cross_validate.collect`` reads."""
    return types.SimpleNamespace(
        model="cnn",  # feature_mode=False: raw windows, not log-mel features
        window_sec=args.window_sec,
        hop_sec=args.hop_sec,
        max_windows_per_record=args.max_windows_per_record,
        window_selection=args.window_selection,
        min_annotated_fraction=args.min_annotated_fraction,
        silence_rms=args.silence_rms,
    )


def load_windows(args: argparse.Namespace, log=print) -> dict:
    """Collect (or load from ``--windows-cache``) the annotated windows.

    The collection path is ``cross_validate.collect`` - the exact function that
    produced the published baseline - so the window protocol (2 s / 1 s hop,
    annotated fraction >= 0.5, at most 4 windows per recording, silence gate
    1e-4, float16 storage) cannot differ between this experiment and the
    baseline.  The report records the counts and warns if they differ from the
    published 11,728 windows / 874 subjects.
    """
    import numpy as np

    import cross_validate

    cache_dir = Path(args.windows_cache) if args.windows_cache else None
    if cache_dir is not None and (cache_dir / "meta.json").exists():
        meta = json.loads((cache_dir / "meta.json").read_text(encoding="utf-8"))
        dataset = {
            key: np.load(cache_dir / f"{key}.npy", allow_pickle=(key in ("subject", "record")))
            for key in ("x", "y", "subject", "record")
        }
        dataset["skipped_silent"] = int(meta["skipped_silent"])
        dataset["skipped_unannotated"] = int(meta["skipped_unannotated"])
        log(f"[data] windows cache hit: {cache_dir} ({len(dataset['y'])} windows)")
        return dataset

    rows = cross_validate.load_rows(Path(args.data_dir))
    log(f"[data] {len(rows)} labelled recordings")
    dataset = cross_validate.collect(rows, collection_args(args))
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for key in ("x", "y", "subject", "record"):
            np.save(cache_dir / f"{key}.npy", dataset[key])
        (cache_dir / "meta.json").write_text(
            json.dumps(
                {
                    "skipped_silent": int(dataset["skipped_silent"]),
                    "skipped_unannotated": int(dataset["skipped_unannotated"]),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        log(f"[data] windows cache written: {cache_dir}")
    return dataset


def train_architecture(cache: dict, settings, architecture: str, log=print) -> tuple:
    """Train one architecture on one fold's train/val windows.

    Byte-for-byte the training recipe of ``train_cnn.train_model`` (30 epochs,
    early stop on validation window AUROC with patience 6, batch 64, Adam
    lr 1e-3 / weight decay 1e-4, ``pos_weight = negatives / positives``, the
    best validation checkpoint restored) with one difference that is the point
    of the experiment: ``hs_cnn.build_model(architecture)`` instead of the
    hard-coded ``Small1DCNN``.  Nothing is tuned per arm.
    """
    import numpy as np
    import torch
    from torch import nn
    from torch.utils.data import DataLoader

    import hs_cnn
    from hs_metrics import binary_metrics

    from train_cnn import WindowDataset, patient_level, predict

    torch.manual_seed(settings.seed)
    np.random.seed(settings.seed)
    train = cache["train"]
    val = cache["val"]
    model = hs_cnn.build_model(architecture)
    positives = max(int(np.sum(train["y"] == 1)), 1)
    negatives = max(int(np.sum(train["y"] == 0)), 1)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([negatives / positives]))
    optimizer = torch.optim.Adam(
        model.parameters(), lr=settings.learning_rate, weight_decay=settings.weight_decay
    )
    loader = DataLoader(
        WindowDataset(train["x"], train["y"]),
        batch_size=settings.batch_size,
        shuffle=True,
        num_workers=0,
    )
    best_state = None
    best_score = -1.0
    best_epoch = 0
    history = []
    stale_epochs = 0

    for epoch in range(1, settings.epochs + 1):
        model.train()
        losses = []
        for windows, labels in loader:
            optimizer.zero_grad()
            logits = model(windows)
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            losses.append(float(loss.item()))
        val_probabilities = predict(model, val["x"])
        val_metrics = patient_level(val_probabilities, val["y"], val["subject"], 0.5)
        val_window_metrics = binary_metrics(val["y"], val_probabilities, 0.5)
        score = val_window_metrics["auroc"]
        history.append(
            {
                "epoch": epoch,
                "loss": float(np.mean(losses)),
                "val_window_auroc": float(score),
                "val_patient_auroc": float(val_metrics["auroc"]),
            }
        )
        log(
            f"[{architecture}] epoch {epoch:3d} loss={np.mean(losses):.4f} "
            f"val_window_auroc={score:.3f} val_patient_auroc={val_metrics['auroc']:.3f}"
        )
        if score > best_score:
            best_score = score
            best_epoch = epoch
            best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= settings.patience:
                log(f"[{architecture}] early stop at epoch {epoch}")
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, {"best_epoch": best_epoch, "best_val_window_auroc": float(best_score), "history": history}


def run_arm(
    architecture: str,
    dataset: dict,
    labels: dict[str, int],
    folds: list[set[str]],
    folds_provenance: dict,
    args: argparse.Namespace,
    log=print,
) -> dict:
    """Run the frozen 5-fold protocol for one architecture.

    The fold loop mirrors ``cross_validate.main`` (same frozen folds, same
    ``split_train_val`` carve with ``seed + fold``, same
    ``best_threshold``-on-validation / evaluate-on-test split, same patient
    aggregation) so an arm's report is directly comparable with the published
    ``cross_validate.py`` report.  What it adds is per-fold wall time and the
    per-patient test probability of every fold, which the pooled bootstrap needs.
    """
    import numpy as np

    import cross_validate
    from hs_metrics import best_threshold, binary_metrics, bootstrap_ci

    from train_cnn import predict

    settings = settings_from_args(args)
    fold_indices = [int(value) for value in str(args.only_folds).split(",") if value.strip()]
    selected = [(index, folds[index]) for index in fold_indices] if fold_indices else list(enumerate(folds))

    pooled_probability: list[float] = []
    pooled_truth: list[int] = []
    pooled_subject: list[str] = []
    pooled_fold: list[int] = []
    per_fold: list[dict] = []
    training_rows: list[dict] = []
    started_arm = time.perf_counter()

    for fold_index, test_subjects in selected:
        pool = set(labels) - test_subjects
        train_subjects, val_subjects = cross_validate.split_train_val(
            pool, labels, args.val_ratio, args.seed + fold_index
        )
        train_idx = cross_validate.indices_for(dataset["subject"], train_subjects)
        val_idx = cross_validate.indices_for(dataset["subject"], val_subjects)
        test_idx = cross_validate.indices_for(dataset["subject"], test_subjects)
        if len(train_idx) == 0 or len(val_idx) == 0 or len(test_idx) == 0:
            raise SystemExit(f"fold {fold_index} has an empty train/val/test window set")
        cache = {
            split: {
                "x": dataset["x"][index],
                "y": dataset["y"][index],
                "subject": dataset["subject"][index],
                "record": dataset["record"][index],
            }
            for split, index in (("train", train_idx), ("val", val_idx))
        }
        started = time.perf_counter()
        model, training = train_architecture(cache, settings, architecture, log=log)
        train_seconds = time.perf_counter() - started
        started_predict = time.perf_counter()
        val_probability = predict(model, dataset["x"][val_idx])
        test_probability = predict(model, dataset["x"][test_idx])
        predict_seconds = time.perf_counter() - started_predict
        training_rows.append(
            {
                "fold": fold_index,
                "train_seconds": float(train_seconds),
                "predict_seconds": float(predict_seconds),
                "epochs_run": len(training["history"]),
                "best_epoch": int(training["best_epoch"]),
                "best_val_window_auroc": float(training["best_val_window_auroc"]),
            }
        )
        threshold, val_score = best_threshold(dataset["y"][val_idx], val_probability)
        window_metrics, patient_metrics = cross_validate.evaluate_split(
            test_probability,
            dataset["y"][test_idx],
            dataset["subject"][test_idx],
            threshold,
        )
        truth, aggregated = cross_validate.patient_probabilities(
            test_probability, dataset["y"][test_idx], dataset["subject"][test_idx]
        )
        subject_order = sorted({str(subject) for subject in dataset["subject"][test_idx]})
        pooled_probability.extend(aggregated.tolist())
        pooled_truth.extend(truth.tolist())
        pooled_subject.extend(subject_order)
        pooled_fold.extend([fold_index] * len(subject_order))
        per_fold.append(
            {
                "fold": fold_index,
                "threshold": float(threshold),
                "val_balanced_accuracy_at_threshold": float(val_score),
                "n_train_subjects": len(train_subjects),
                "n_val_subjects": len(val_subjects),
                "n_test_subjects": len(test_subjects),
                "n_test_windows": int(len(test_idx)),
                "train_seconds": float(train_seconds),
                "epochs_run": len(training["history"]),
                "best_epoch": int(training["best_epoch"]),
                "window": window_metrics,
                "patient": patient_metrics,
            }
        )
        log(
            f"[{architecture}] fold {fold_index} "
            f"threshold={threshold:.4f} window_auroc={window_metrics['auroc']:.3f} "
            f"patient_auroc={patient_metrics['auroc']:.3f} "
            f"patient_ba={patient_metrics['balanced_accuracy']:.3f} "
            f"train={train_seconds / 60:.1f}min epochs={len(training['history'])}"
        )

    arm_seconds = time.perf_counter() - started_arm

    def aggregate(key: str, field: str) -> dict:
        values = np.asarray([fold[key][field] for fold in per_fold], dtype=float)
        return {
            "mean": float(np.nanmean(values)),
            "std": float(np.nanstd(values)),
            "values": values.tolist(),
        }

    truth_array = np.asarray(pooled_truth, dtype=int)
    probability_array = np.asarray(pooled_probability, dtype=float)
    pooled_patient = binary_metrics(truth_array, probability_array, 0.5)
    pooled_patient["auroc_ci95"] = bootstrap_ci(
        truth_array, probability_array, "auroc", n_boot=args.bootstrap
    )
    pooled_patient["balanced_accuracy_ci95"] = bootstrap_ci(
        truth_array, probability_array, "balanced_accuracy", 0.5, n_boot=args.bootstrap
    )
    budget = architecture_budget(architecture)
    report = {
        "task": "CirCor murmur Present vs Absent",
        "model": architecture,
        "architecture": budget,
        "folds": len(selected),
        "folds_evaluated": [fold["fold"] for fold in per_fold],
        "window_selection": args.window_selection,
        "min_annotated_fraction": args.min_annotated_fraction,
        "max_windows_per_record": args.max_windows_per_record,
        "windows_total": int(len(dataset["y"])),
        "subjects_total": len(labels),
        "skipped_silent": dataset["skipped_silent"],
        "skipped_unannotated": dataset["skipped_unannotated"],
        "folds_source": folds_provenance,
        "seeds": {
            "cv_seed": int(args.seed),
            "val_split_rule": "seed + fold_index",
            "val_ratio": float(args.val_ratio),
        },
        "hyperparameters": {
            "epochs_max": int(args.epochs),
            "patience": int(args.patience),
            "batch_size": int(args.batch_size),
            "learning_rate": float(args.learning_rate),
            "weight_decay": float(args.weight_decay),
            "optimizer": "Adam",
            "loss": "BCEWithLogitsLoss(pos_weight=negatives/positives)",
            "early_stop_metric": "validation window AUROC",
            "augmentation": "none",
        },
        "threads": {
            "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", ""),
            "torch_num_threads": int(torch_num_threads()),
        },
        "training": {
            "per_fold": training_rows,
            "total_seconds": float(arm_seconds),
            "train_seconds": float(sum(row["train_seconds"] for row in training_rows)),
            "mean_seconds_per_fold": float(arm_seconds / max(len(training_rows), 1)),
        },
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
    return {
        "report": report,
        "pooled": {
            "fold": pooled_fold,
            "subject": pooled_subject,
            "truth": pooled_truth,
            "probability": pooled_probability,
        },
    }


def torch_num_threads() -> int:
    try:
        import torch

        return int(torch.get_num_threads())
    except Exception:  # noqa: BLE001 - torch is optional for the report writer
        return 0


def paired_bootstrap_delta(
    baseline: dict,
    arm: dict,
    n_boot: int = 2000,
    seed: int = 20261008,
) -> dict:
    """Patient-level paired bootstrap of ``AUROC(arm) - AUROC(baseline)``.

    Both arms score exactly the same patients (same frozen folds), so the
    resampling is paired and the interval is on the *difference* - a tighter and
    more relevant statement than comparing two independent marginal CIs.  This
    is reported as supporting evidence; the pre-registered rule uses the
    baseline's marginal CI half-width.
    """
    import numpy as np

    from hs_metrics import auroc

    baseline_map = {
        subject: (int(truth), float(probability))
        for subject, truth, probability in zip(
            baseline["subject"], baseline["truth"], baseline["probability"]
        )
    }
    pairs = [
        (baseline_map[subject], (int(truth), float(probability)))
        for subject, truth, probability in zip(arm["subject"], arm["truth"], arm["probability"])
        if subject in baseline_map
    ]
    if not pairs:
        return {"low": float("nan"), "high": float("nan"), "n_boot": 0, "n_patients": 0}
    truth = np.asarray([pair[0][0] for pair in pairs], dtype=int)
    baseline_probability = np.asarray([pair[0][1] for pair in pairs], dtype=float)
    arm_probability = np.asarray([pair[1][1] for pair in pairs], dtype=float)
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(n_boot):
        index = rng.integers(0, len(truth), len(truth))
        sample_truth = truth[index]
        if len(np.unique(sample_truth)) < 2:
            continue
        deltas.append(
            auroc(sample_truth, arm_probability[index])
            - auroc(sample_truth, baseline_probability[index])
        )
    values = np.asarray(deltas, dtype=float)
    return {
        "low": float(np.percentile(values, 2.5)),
        "high": float(np.percentile(values, 97.5)),
        "mean": float(values.mean()),
        "n_boot": int(len(values)),
        "n_patients": int(len(truth)),
    }


def decide_compute_ceiling(baseline: dict, arms: list[dict], n_folds_required: int = 5) -> dict:
    """Apply the pre-registered rule to one control arm and the capacity arms.

    ``baseline`` and each entry of ``arms`` are one arm's ``report`` dict.  The
    rule and its asymmetry are fixed in :data:`DECISION_RULE`; this function is
    pure so the test suite can pin both outcomes (win and no-win) without
    training anything.
    """
    if baseline["folds"] < n_folds_required:
        return {
            "evaluated": False,
            "reason": (
                f"only {baseline['folds']} of {n_folds_required} frozen folds were "
                "evaluated (smoke run): the pre-registered rule requires the full protocol"
            ),
            "triggered": False,
        }
    partial = [arm["model"] for arm in arms if arm["folds"] < n_folds_required]
    if partial:
        return {
            "evaluated": False,
            "reason": (
                f"arm(s) {partial} did not evaluate all {n_folds_required} frozen "
                "folds (smoke run): the pre-registered rule requires the full protocol"
            ),
            "triggered": False,
        }
    baseline_pooled = float(baseline["pooled_patient_at_0.5"]["auroc"])
    ci = baseline["pooled_patient_at_0.5"]["auroc_ci95"]
    half_width = float((ci["high"] - ci["low"]) / 2.0)
    comparison = []
    for arm in arms:
        arm_pooled = float(arm["pooled_patient_at_0.5"]["auroc"])
        delta = arm_pooled - baseline_pooled
        comparison.append(
            {
                "model": arm["model"],
                "pooled_patient_auroc": arm_pooled,
                "delta_vs_baseline": delta,
                "delta_over_baseline_half_width": delta / half_width if half_width > 0 else float("inf"),
                "exceeds_half_width": bool(delta > half_width),
                "delta_over_baseline_fold_std": (
                    delta / float(baseline["aggregate"]["patient_auroc"]["std"])
                    if baseline["aggregate"]["patient_auroc"]["std"] > 0
                    else float("inf")
                ),
                "parameters": int(arm["architecture"]["parameters"]),
                "macs_per_window": int(arm["architecture"]["macs_per_window"]),
            }
        )
    winner = max(comparison, key=lambda row: row["pooled_patient_auroc"])
    triggered = bool(winner["exceeds_half_width"])
    return {
        "evaluated": True,
        "baseline_model": baseline["model"],
        "baseline_pooled_patient_auroc": baseline_pooled,
        "baseline_half_width": half_width,
        "baseline_fold_std": float(baseline["aggregate"]["patient_auroc"]["std"]),
        "comparison": comparison,
        "best_arm": winner,
        "triggered": triggered,
        "required_model": (
            {
                "parameters": winner["parameters"],
                "macs_per_window": winner["macs_per_window"],
                "model": winner["model"],
            }
            if triggered
            else None
        ),
    }


def write_probability_csv(path: Path, pooled: dict) -> Path:
    """One row per test patient: ``fold,subject_id,label,probability``.

    Test patients only (each patient appears once, in the fold it was held out
    of), which is what the pooled metrics and the paired bootstrap use.  No
    threshold or calibration is fitted here - this is a report artifact so a
    reviewer can recompute every pooled number in the report.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        (fold, subject, label, probability)
        for fold, subject, label, probability in zip(
            pooled["fold"], pooled["subject"], pooled["truth"], pooled["probability"]
        )
    ]
    rows.sort(key=lambda row: (row[0], row[1]))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("fold", "subject_id", "label", "probability"))
        for fold, subject, label, probability in rows:
            writer.writerow((int(fold), str(subject), int(label), repr(float(probability))))
    return path


def check_control_against_baseline(control: dict, baseline_path: Path) -> dict:
    """Does the re-run control arm reproduce the published baseline?

    The capacity comparison is only honest if the control arm - trained by this
    driver, on this machine, at this thread count - lands where the published
    baseline landed.  A mismatch is reported, not hidden: the comparison would
    then be against the re-run control, not the published number.
    """
    if not baseline_path.exists():
        return {"checked": False, "reason": f"{baseline_path} not found"}
    published = json.loads(baseline_path.read_text(encoding="utf-8"))
    published_auroc = float(published["pooled_patient_at_0.5"]["auroc"])
    published_mean = float(published["aggregate"]["patient_auroc"]["mean"])
    control_auroc = float(control["pooled_patient_at_0.5"]["auroc"])
    control_mean = float(control["aggregate"]["patient_auroc"]["mean"])
    per_fold = []
    for expected, actual in zip(published["per_fold"], control["per_fold"]):
        if expected["fold"] != actual["fold"]:
            continue
        per_fold.append(
            {
                "fold": int(actual["fold"]),
                "published_patient_auroc": float(expected["patient"]["auroc"]),
                "control_patient_auroc": float(actual["patient"]["auroc"]),
                "delta": float(actual["patient"]["auroc"] - expected["patient"]["auroc"]),
            }
        )
    max_delta = max((abs(row["delta"]) for row in per_fold), default=float("nan"))
    return {
        "checked": True,
        "published_report": str(baseline_path),
        "published_pooled_patient_auroc": published_auroc,
        "control_pooled_patient_auroc": control_auroc,
        "pooled_delta": control_auroc - published_auroc,
        "published_mean_fold_patient_auroc": published_mean,
        "control_mean_fold_patient_auroc": control_mean,
        "per_fold": per_fold,
        "max_abs_fold_delta": max_delta,
        "reproduces_within_0.01": bool(max_delta <= 0.01),
        "note": (
            "The control arm is what the capacity arms are compared against. If "
            "this does not reproduce the published baseline, the difference is a "
            "machine/thread/library effect and the comparison uses the re-run "
            "control, never the published number."
        ),
    }


def verdict_text(decision: dict) -> str:
    """The one-paragraph answer, generated from the numbers (not hard-coded)."""
    if not decision.get("evaluated"):
        return f"Not evaluated: {decision.get('reason')}."
    baseline_auroc = decision["baseline_pooled_patient_auroc"]
    half = decision["baseline_half_width"]
    rows = sorted(decision["comparison"], key=lambda row: row["pooled_patient_auroc"], reverse=True)
    best = rows[0]
    if decision["triggered"]:
        required = decision["required_model"]
        return (
            f"The compute ceiling is real: the best capacity arm ({best['model']}, "
            f"{required['parameters']:,} parameters, {required['macs_per_window'] / 1e6:.1f} M "
            f"MAC/window) reaches pooled patient AUROC {best['pooled_patient_auroc']:.3f} "
            f"against the small CNN's {baseline_auroc:.3f}. The gain of "
            f"{best['delta_vs_baseline']:+.3f} is larger than the control arm's "
            f"{half:.3f} CI half-width, so it is not explained by patient resampling. "
            f"On this data a host that can run a ~{required['parameters'] / 1000:.0f}k-parameter, "
            f"~{required['macs_per_window'] / 1e6:.0f} M-MAC/window model is justified."
        )
    return (
        f"The improvement is within noise. The best capacity arm ({best['model']}, "
        f"{best['parameters']:,} parameters, {best['macs_per_window'] / 1e6:.1f} M MAC/window, "
        f"{best['parameters'] / max(architecture_parameters('small'), 1):.1f}x the small CNN) "
        f"reaches pooled patient AUROC {best['pooled_patient_auroc']:.3f} against the small CNN's "
        f"{baseline_auroc:.3f}: a change of {best['delta_vs_baseline']:+.3f}, which is "
        f"{abs(best['delta_vs_baseline']) / half:.2f} of the {half:.3f} CI half-width and "
        f"{abs(best['delta_vs_baseline']) / max(decision['baseline_fold_std'], 1e-12):.2f} of the "
        f"control arm's {decision['baseline_fold_std']:.3f} fold-to-fold standard deviation. "
        "That is patient-resampling noise, not a compute ceiling. With 874 patients / 179 "
        "positives the data is the bottleneck: a Raspberry Pi able to run a "
        f"{best['macs_per_window'] / max(architecture_macs('small'), 1):.0f}x larger model "
        "would not buy accuracy, so the platform change (1-2 h vs ~20 h battery, 20-40 s boot, "
        "5-15x cost) would pay for capacity this data cannot use. Stay on the ESP32-S3 host, or "
        "justify the Pi on non-accuracy grounds (connectivity, storage, on-device UI) instead."
    )


def write_arm_markdown(path: Path, arm: dict) -> None:
    report = arm["report"]
    budget = report["architecture"]
    lines = [
        f"# {report['model']} arm - patient-level cross-validation",
        "",
        f"- Architecture: {budget['description']}",
        f"- Parameters: {budget['parameters']:,} ({budget['parameter_ratio_to_small']:.2f}x the small CNN)",
        f"- MACs / window: {budget['macs_per_window']:,} ({budget['macs_ratio_to_small']:.2f}x the small CNN)",
        f"- Folds: {report['folds']} (frozen splits, sha256 `{report['folds_source']['fingerprint']}`)",
        f"- Windows: {report['windows_total']}, subjects: {report['subjects_total']}",
        f"- Seed: {report['seeds']['cv_seed']} (val carve = seed + fold, ratio {report['seeds']['val_ratio']})",
        f"- Threads: OMP_NUM_THREADS={report['threads']['OMP_NUM_THREADS']!r}, "
        f"torch={report['threads']['torch_num_threads']}",
        f"- Training time: {report['training']['train_seconds'] / 60:.1f} min total, "
        f"{report['training']['mean_seconds_per_fold'] / 60:.1f} min/fold (wall, including validation)",
        "",
        "| Fold | Test subjects | Window AUROC | Patient AUROC | Patient BA | Train (min) | Epochs |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for fold in report["per_fold"]:
        lines.append(
            "| {fold} | {n} | {wa:.3f} | {pa:.3f} | {pb:.3f} | {train:.1f} | {epochs} |".format(
                fold=fold["fold"],
                n=fold["n_test_subjects"],
                wa=fold["window"]["auroc"],
                pa=fold["patient"]["auroc"],
                pb=fold["patient"]["balanced_accuracy"],
                train=fold["train_seconds"] / 60.0,
                epochs=fold["epochs_run"],
            )
        )
    aggregate = report["aggregate"]
    pooled = report["pooled_patient_at_0.5"]
    lines.extend(
        [
            "",
            "## Aggregate (mean ± std across folds)",
            "",
            f"- Patient AUROC: {aggregate['patient_auroc']['mean']:.3f} ± "
            f"{aggregate['patient_auroc']['std']:.3f}",
            f"- Patient balanced accuracy: {aggregate['patient_balanced_accuracy']['mean']:.3f} ± "
            f"{aggregate['patient_balanced_accuracy']['std']:.3f}",
            f"- Window AUROC: {aggregate['window_auroc']['mean']:.3f} ± "
            f"{aggregate['window_auroc']['std']:.3f}",
            f"- Pooled patient AUROC: {pooled['auroc']:.3f} "
            f"(95% CI {pooled['auroc_ci95']['low']:.3f}-{pooled['auroc_ci95']['high']:.3f})",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_comparison_markdown(path: Path, summary: dict) -> None:
    decision = summary["decision"]
    lines = [
        "# Does a larger model buy accuracy? (capacity sweep)",
        "",
        "**Question.** Is the ESP32-S3 host the accuracy bottleneck? If a model 5-11x",
        "larger than the 41,105-parameter small CNN lands within noise of it, then",
        "compute is not the bottleneck at 874 patients / 179 positives, and replacing",
        "the host with a Raspberry Pi (1-2 h vs ~20 h battery, 20-40 s boot, 5-15x",
        "cost) would pay for capacity this data cannot use.",
        "",
        "**Pre-registered decision rule** (fixed before the large arms ran, see",
        "`tools/ai/large_model_experiment.py:DECISION_RULE`):",
        "",
        f"> {DECISION_RULE}",
        "",
        "## Protocol (identical for every arm)",
        "",
        f"- Frozen patient folds: `{summary['protocol']['splits_file']}` "
        f"(sha256 `{summary['protocol']['fingerprint']}`), {summary['protocol']['n_folds']} folds",
        f"- Windows: {summary['protocol']['window_selection']} windows, 2 s / 1 s hop, "
        f"max {summary['protocol']['max_windows_per_record']} per recording, "
        f"annotated fraction >= {summary['protocol']['min_annotated_fraction']}",
        f"- Data: {summary['protocol']['windows_total']} windows, "
        f"{summary['protocol']['subjects_total']} patients",
        f"- Hyper-parameters: {summary['protocol']['hyperparameters']}",
        f"- Threads: OMP_NUM_THREADS={summary['protocol']['threads']!r}",
        "",
        "## Arms",
        "",
        "| Arm | Parameters | MACs/window | Patient AUROC (mean ± std) | Patient BA | Pooled AUROC (95% CI) | Window AUROC | Train (min) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in summary["arm_order"]:
        report = summary["arms"][name]
        pooled = report["pooled_patient_at_0.5"]
        lines.append(
            "| {name} | {params:,} | {macs:.1f} M | {auroc:.3f} ± {std:.3f} | {ba:.3f} | "
            "{pooled:.3f} ({low:.3f}-{high:.3f}) | {window:.3f} | {minutes:.1f} |".format(
                name=name,
                params=report["architecture"]["parameters"],
                macs=report["architecture"]["macs_per_window"] / 1e6,
                auroc=report["aggregate"]["patient_auroc"]["mean"],
                std=report["aggregate"]["patient_auroc"]["std"],
                ba=report["aggregate"]["patient_balanced_accuracy"]["mean"],
                pooled=pooled["auroc"],
                low=pooled["auroc_ci95"]["low"],
                high=pooled["auroc_ci95"]["high"],
                window=report["aggregate"]["window_auroc"]["mean"],
                minutes=report["training"]["train_seconds"] / 60.0,
            )
        )
    lines.extend(["", "## Comparison against the small CNN", ""])
    if not decision.get("evaluated"):
        lines.extend([f"Decision rule not evaluable: {decision.get('reason')}.", ""])
    else:
        lines.extend(
            [
                f"Control arm pooled patient AUROC: **{decision['baseline_pooled_patient_auroc']:.3f}** "
                f"(95% CI half-width **{decision['baseline_half_width']:.3f}**); control "
                f"fold-to-fold std {decision['baseline_fold_std']:.3f}.",
                "",
                "| Arm | Pooled AUROC | Δ vs control | Δ / CI half-width | Δ / fold std | Paired bootstrap 95% CI of Δ | Exceeds half-width? |",
                "| --- | ---: | ---: | ---: | ---: | ---: | :---: |",
            ]
        )
        for row in decision["comparison"]:
            paired = summary["paired_deltas"].get(row["model"], {})
            lines.append(
                "| {model} | {pooled:.3f} | {delta:+.3f} | {over_half:.2f}x | {over_std:.2f}x | "
                "{low:+.3f} to {high:+.3f} | {exceeds} |".format(
                    model=row["model"],
                    pooled=row["pooled_patient_auroc"],
                    delta=row["delta_vs_baseline"],
                    over_half=row["delta_over_baseline_half_width"],
                    over_std=row["delta_over_baseline_fold_std"],
                    low=paired.get("low", float("nan")),
                    high=paired.get("high", float("nan")),
                    exceeds="yes" if row["exceeds_half_width"] else "no",
                )
            )
        lines.extend(
            [
                "",
                f"**Decision rule triggered: {'YES' if decision['triggered'] else 'NO'}.**",
                "",
                "## Host-side consequences (E4 engineering estimate, not a board measurement)",
                "",
                "| Arm | int8 weights | MAC/window | MAC/s at 0.5 windows/s |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for name in summary["arm_order"]:
            architecture = summary["arms"][name]["architecture"]
            lines.append(
                "| {name} | {kib:.0f} KiB | {macs:.1f} M | {rate:.1f} MMAC/s |".format(
                    name=name,
                    kib=architecture["int8_weight_bytes_estimate"] / 1024.0,
                    macs=architecture["macs_per_window"] / 1e6,
                    rate=architecture["macs_per_window"] * 0.5 / 1e6,
                )
            )
        lines.extend(
            [
                "",
                "The 41,105-parameter baseline is a design constraint, not a law: a",
                "463k-parameter int8 model needs ~450 KiB of weights (PSRAM on an",
                "S3-class part) and ~16 MMAC/s at 0.5 windows/s. Nothing here says a",
                "Raspberry Pi is required, only that accuracy stopped being flat between",
                "41k and the winning arm: the cheapest host that meets the accuracy",
                "target should be re-decided against measured target-board latency,",
                "current and BOM, which this experiment did not measure.",
                "",
                "## Verdict",
                "",
                summary["verdict"],
                "",
                "## Control-arm check against the published baseline",
                "",
            ]
        )
        control = summary["control_check"]
        if control.get("checked"):
            lines.append(
                f"- Published pooled patient AUROC {control['published_pooled_patient_auroc']:.3f} "
                f"vs re-run control {control['control_pooled_patient_auroc']:.3f} "
                f"(Δ {control['pooled_delta']:+.3f}); max per-fold |Δ| "
                f"{control['max_abs_fold_delta']:.3f} -> reproduces within 0.01: "
                f"**{control['reproduces_within_0.01']}**."
            )
            lines.append(f"- {control['note']}")
        else:
            lines.append(f"- Not checked: {control.get('reason')}")
    lines.extend(
        [
            "",
            "## What this does not establish",
            "",
            "- **It is not a hyper-parameter search.** Every arm uses the baseline's",
            "  recipe (30 epochs, patience 6, batch 64, lr 1e-3, weight decay 1e-4,",
            "  seed 20261008 from `cross_validate.py`). A tuned large model might do",
            "  better; that is a different experiment and would need its own nested",
            "  validation to stay out of the test folds.",
            "- **One seed, five folds.** Fold-to-fold std and the patient bootstrap",
            "  describe this cohort, not training-seed variance.",
            "- **Capacity is added where the baseline already operates.** Both capacity",
            "  arms keep the baseline's front end (stem + its 2nd and 3rd ConvBlocks,",
            "  i.e. the same downsampling and the same aliasing) and replace the",
            "  baseline's last 64-channel ConvBlock with an 8-block trunk on the",
            "  same 62-frame map. The arms therefore differ from the control in",
            "  capacity, not in access to temporal detail. A high-resolution trunk",
            "  is much more expensive (MACs grow with frames x parameters) and was",
            "  not run; if such a model wins, the ceiling is real for a different",
            "  reason than parameter count.",
            "- **CPU wall time, 4 threads.** Training time is a PC measurement, not a",
            "  Pi or ESP32-S3 measurement; it bounds the experiment, not deployment.",
            "- **\"Capacity helps\" is not the same as \"a Raspberry Pi is the cheapest",
            "  host that has it\".** Whether an ESP32-S3-class host can serve the",
            "  winning arm at the project's 0.5 windows/s is an E4 engineering",
            "  estimate (MAC/s and int8 weight bytes below), not a board measurement;",
            "  an ESP32-P4, an STM32N6 or more int8 work could meet the same accuracy",
            "  target without the Pi's battery, boot and cost penalties.",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def git_output(arguments: list[str]) -> str:
    """Best-effort `git <arguments>` output, empty string if git is unavailable."""
    import subprocess

    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance must never break a run
        return ""


def protocol_record(
    args: argparse.Namespace,
    *,
    splits_file: str,
    fingerprint: str,
    n_folds: int,
    windows_total: int,
    subjects_total: int,
    threads: str,
    torch_threads: int,
) -> dict:
    """The protocol block shared by a live run and a ``--report-only`` rebuild."""
    return {
        "splits_file": str(splits_file),
        "fingerprint": fingerprint,
        "n_folds": int(n_folds),
        "window_selection": args.window_selection,
        "max_windows_per_record": args.max_windows_per_record,
        "min_annotated_fraction": args.min_annotated_fraction,
        "windows_total": int(windows_total),
        "subjects_total": int(subjects_total),
        "hyperparameters": {
            "epochs_max": args.epochs,
            "patience": args.patience,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "weight_decay": args.weight_decay,
            "seed": args.seed,
            "val_ratio": args.val_ratio,
        },
        "threads": threads,
        "torch_num_threads": int(torch_threads),
    }


def load_saved_arm(out_dir: Path, name: str) -> dict:
    """Reload one arm written by a previous run (``--report-only``)."""
    report = json.loads((out_dir / name / "report.json").read_text(encoding="utf-8"))
    csv_path = out_dir / name / "patient-probabilities.csv"
    pooled = {"fold": [], "subject": [], "truth": [], "probability": []}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            pooled["fold"].append(int(row["fold"]))
            pooled["subject"].append(row["subject_id"])
            pooled["truth"].append(int(row["label"]))
            pooled["probability"].append(float(row["probability"]))
    return {"report": report, "pooled": pooled}


def write_summary(
    out_dir: Path,
    arms: dict,
    arm_order: list[str],
    args: argparse.Namespace,
    protocol: dict,
    started_at_utc: str,
    argv: list[str],
    log=print,
    previous: dict | None = None,
) -> dict:
    """Assemble and write the capacity comparison (``report.json`` + ``report.md``).

    Pure with respect to training: it only reads the arm results, so a
    ``--report-only`` rebuild and a live run produce identical numbers.  When
    ``previous`` is the summary a live run already wrote, its training
    provenance (start time, command, git state) is preserved and the rebuild is
    recorded separately, so a re-rendered report still says when it was trained.
    """
    regenerated_at_utc = None
    if previous:
        regenerated_at_utc = started_at_utc
        started_at_utc = previous.get("started_at_utc", started_at_utc)
        command = previous.get("command", " ".join([Path(sys.argv[0]).name, *argv]))
        repository = previous.get("repository", {})
    else:
        command = " ".join([Path(sys.argv[0]).name, *argv])
        repository = {
            "commit": git_output(["rev-parse", "HEAD"]),
            "describe": git_output(["describe", "--always", "--dirty"]),
            "dirty_paths": git_output(["status", "--porcelain"]).split("\n")
            if git_output(["status", "--porcelain"])
            else [],
        }
    if "small" not in arms:
        log("[warn] no 'small' control arm available: writing arm reports only")
        return
    control = arms["small"]["report"]
    control_check = check_control_against_baseline(control, Path(args.baseline_report))
    capacity_arms = [arms[name]["report"] for name in arm_order if name != "small"]
    decision = decide_compute_ceiling(control, capacity_arms)
    paired_deltas = {
        name: paired_bootstrap_delta(arms["small"]["pooled"], arms[name]["pooled"], n_boot=args.bootstrap)
        for name in arm_order
        if name != "small"
    }
    summary = {
        "task": "CirCor murmur Present vs Absent - does model capacity buy patient accuracy?",
        "started_at_utc": started_at_utc,
        "command": command,
        "repository": repository,
        "report_regenerated_at_utc": regenerated_at_utc,
        "report_regenerated_with": (
            " ".join([Path(sys.argv[0]).name, *argv]) if regenerated_at_utc else None
        ),
        "question": (
            "Would replacing the ESP32-S3 host with a Raspberry Pi ('more compute allows a "
            "stronger model') buy accuracy on this data, or is the data the bottleneck?"
        ),
        "decision_rule": DECISION_RULE,
        "decision": decision,
        "verdict": verdict_text(decision),
        "arm_order": arm_order,
        "arms": {name: arms[name]["report"] for name in arm_order},
        "paired_deltas": paired_deltas,
        "control_check": control_check,
        "protocol": protocol,
        "evidence_levels": {
            "architecture_budget": "E4 engineering estimate from the model graph (analytic, pinned by tests)",
            "accuracy": "independent local measurement on the frozen CirCor protocol, 4 CPU threads",
            "target_board": "pending: no Pi or ESP32-S3 has been measured here",
        },
    }
    (out_dir / "report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_comparison_markdown(out_dir / "report.md", summary)
    print(json.dumps(
        {
            "decision_rule_triggered": decision.get("triggered"),
            "verdict": summary["verdict"],
            "arms": {
                name: {
                    "parameters": arms[name]["report"]["architecture"]["parameters"],
                    "macs_per_window": arms[name]["report"]["architecture"]["macs_per_window"],
                    "patient_auroc_mean": arms[name]["report"]["aggregate"]["patient_auroc"]["mean"],
                    "patient_auroc_std": arms[name]["report"]["aggregate"]["patient_auroc"]["std"],
                    "pooled_patient_auroc": arms[name]["report"]["pooled_patient_at_0.5"]["auroc"],
                    "train_minutes": arms[name]["report"]["training"]["train_seconds"] / 60.0,
                }
                for name in arm_order
            },
        },
        indent=2,
        ensure_ascii=False,
    ))
    print(f"[report] {out_dir / 'report.md'}")




def main(argv: list[str] | None = None) -> None:
    started_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv)
    log = (lambda *_: None) if args.quiet else print

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    unknown = [name for name in args.models if name not in CONV_ARCHITECTURES and name not in SMALL_ALIASES]
    if unknown:
        raise SystemExit(f"unknown --models {unknown}; expected {list(CONV_ARCHITECTURES)} (+ 'cnn' alias)")
    if args.models[0] not in SMALL_ALIASES:
        log(
            "[warn] the control arm ('small') is not the first --models entry: the "
            "comparison still uses the small arm if it is run at all"
        )
    names = []
    for requested in args.models:
        name = "small" if requested in SMALL_ALIASES else requested
        if name not in names:
            names.append(name)

    if args.report_only:
        arms = {name: load_saved_arm(out_dir, name) for name in names}
        first = arms[names[0]]["report"]
        protocol = protocol_record(
            args,
            splits_file=first["folds_source"]["path"],
            fingerprint=first["folds_source"]["fingerprint"],
            n_folds=first["folds_source"]["n_folds"],
            windows_total=first["windows_total"],
            subjects_total=first["subjects_total"],
            threads=os.environ.get("OMP_NUM_THREADS", ""),
            torch_threads=torch_num_threads(),
        )
        log(f"[report-only] rebuilt from {len(names)} saved arm report(s) under {out_dir}")
        summary_path = out_dir / "report.json"
        previous = (
            json.loads(summary_path.read_text(encoding="utf-8"))
            if summary_path.exists()
            else None
        )
        write_summary(
            out_dir, arms, names, args, protocol, started_at_utc, raw_argv, log=log, previous=previous
        )
        return

    import cross_validate

    dataset = load_windows(args, log=log)
    labels = cross_validate.subject_labels(dataset)
    folds, n_folds, folds_provenance = cross_validate.resolve_folds(args, labels)
    log(
        f"[protocol] {len(labels)} patients, {len(dataset['y'])} windows, "
        f"{n_folds} frozen folds, fingerprint {folds_provenance['fingerprint']}"
    )
    # The published protocol is 11,728 annotated windows over 874 patients; a
    # different count means the window protocol moved and the comparison would
    # no longer be against the published baseline.
    if len(dataset["y"]) != 11728 or len(labels) != 874:
        log(
            f"[warn] collected {len(dataset['y'])} windows over {len(labels)} patients, "
            "expected 11728 over 874: the frozen window protocol is not reproduced"
        )

    arms: dict[str, dict] = {}
    for name in names:
        log(f"[arm] {name}: {ARCHITECTURE_SPECS[name].description}")
        result = run_arm(name, dataset, labels, folds, folds_provenance, args, log=log)
        arm_dir = out_dir / name
        arm_dir.mkdir(parents=True, exist_ok=True)
        (arm_dir / "report.json").write_text(
            json.dumps(result["report"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        write_arm_markdown(arm_dir / "report.md", result)
        write_probability_csv(arm_dir / "patient-probabilities.csv", result["pooled"])
        arms[name] = result
        log(
            f"[arm] {name} done: pooled patient AUROC "
            f"{result['report']['pooled_patient_at_0.5']['auroc']:.3f} "
            f"({result['report']['training']['total_seconds'] / 60:.1f} min)"
        )

    protocol = protocol_record(
        args,
        splits_file=str(args.splits_file),
        fingerprint=folds_provenance["fingerprint"],
        n_folds=n_folds,
        windows_total=len(dataset["y"]),
        subjects_total=len(labels),
        threads=os.environ.get("OMP_NUM_THREADS", ""),
        torch_threads=torch_num_threads(),
    )
    write_summary(out_dir, arms, names, args, protocol, started_at_utc, raw_argv, log=log)


if __name__ == "__main__":
    main()
