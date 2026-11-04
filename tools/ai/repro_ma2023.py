#!/usr/bin/env python3
"""1:1 reproduction of Ma et al. (2023) on the Yaseen five-class dataset.

What this script is for
-----------------------
The W2 acceptance gate is "the reproduction lands within 98.65 % +- 1.5 points".
This script is that reproduction and nothing else: it deliberately uses the
*paper's* pre-processing (1 kHz, 2 s, min-max, no band-pass), *not* this
project's frozen 2 kHz pipeline (ADR-036/037).  Mixing the two would make the
number incomparable with the paper.

Every value below is tagged with its provenance:

* ``PAPER``  -- stated in Ma et al., Comput. Methods Programs Biomed. 243
  (2024) 107906, doi:10.1016/j.cmpb.2023.107906.  Wording is quoted where the
  exact value matters.
* ``DERIVED``-- not printed in the paper, but fixed by a number it does print
  (the 20,193-parameter count pins ``padding='same'``; see ``build_model``).
* ``E4``     -- the paper is silent, so this is our engineering choice.  Every
  E4 value is also written to ``report.json`` under ``deviations`` so the
  write-up can list them instead of pretending they came from the paper.

Protocol facts taken from the paper
-----------------------------------
* Input: 2,000 samples = 2 s at 1 kHz.  "All PCG samples were downsampled to
  1 kHz and non-overlappingly windowed into 2-second clips."
* Yaseen: "long and zero-padded short recordings were cropped to make all
  samples have an exact length of 2 s", then min-max scaled to [-1, 1].
  Locally that is 950 cropped, 49 zero-padded and 1 already exactly 2 s, so it
  is one clip per recording and the count is exactly 1,000 clips -- the paper's
  window count is not an unknown, it follows from its own rule.  The 49 padded
  clips (4.9 %, mostly MS/MR/MVP) are the only place where the choice of pad
  side can matter.
* Model: 3 x (Conv1D kernel 20, filters 16/8/4, tanh), each followed by
  MaxPool 4; Flatten; Dense 128 tanh; Dense 5 softmax.  20,193 parameters.
* Loss/optimiser: categorical cross-entropy, Adam.
* 10-fold cross-validation, "one attempt of 40": the reported 98.65 %
  (95 % CI 98.555-98.745) is the mean of 40 repeats of the whole 10-fold run,
  so that CI is over attempts, not over folds.

What the paper does not give (E4 here, and recorded in the report)
------------------------------------------------------------------
learning rate (1e-3), batch size (32), epoch budget (100), early stopping on an
inner 10 % validation split of the nine training folds (patience 10), the
resampling method, the crop position/pad side, and the fold seed.  The paper
never states a batch size for training either -- the "batch size equal to 2" in
Section 2.6 belongs to the 1-epoch on-device fine-tuning demo.

Usage
-----
    .venv-tf/bin/python tools/ai/repro_ma2023.py --smoke
    .venv-tf/bin/python tools/ai/repro_ma2023.py --repeats 5 --out data/reports/repro-ma2023

``--smoke`` runs 2 folds for 5 epochs to prove the wiring and the parameter
count; only a full run produces a number that may be compared with 98.65 %.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

# scipy is imported inside the two functions that need it so that this module
# stays importable by the NumPy-only CI job (see .github/workflows/ci.yml); the
# frozen pipeline in tools/preprocess.py has the same constraint.

# --------------------------------------------------------------------------
# PAPER constants.  Do not "improve" these; the whole point is fidelity.
# --------------------------------------------------------------------------
FS_ORIG = 8_000                # PAPER (Yaseen recordings), confirmed locally
FS_TARGET = 1_000              # PAPER "downsampled to 1 kHz"
N_SAMPLES = 2_000              # PAPER "2-second clips" = 2,000 points at 1 kHz
CONV_FILTERS = (16, 8, 4)      # PAPER "16, 8 and 4, respectively"
KERNEL_SIZE = 20               # PAPER "size of kernels in all three convolutional layers was 20"
POOL_SIZE = 4                  # PAPER "kernel size of all three pooling layers was 4"
DENSE_UNITS = 128              # PAPER "one fully connected dense layer with 128 units"
CLASSES = ("AS", "MS", "MR", "MVP", "N")   # ADR-037 froze this order; the paper's order is irrelevant
N_FOLDS = 10                   # PAPER "ten-fold cross-validation"
EXPECTED_PARAMS = 20_193       # PAPER "only contains 20,193 parameters"
PAPER_ACC = 98.65              # PAPER average 10-fold accuracy (%)
PAPER_CI = (98.555, 98.745)    # PAPER 95 % CI over 40 attempts
PAPER_ATTEMPTS = 40            # PAPER "taking it as one attempt of 40"

DEFAULT_RAW = Path("data/raw/yaseen/extracted")
DEFAULT_OUT = Path("data/reports/repro-ma2023")


def parameter_breakdown(padding: str = "same") -> dict:
    """Pure-NumPy recount of the layer arithmetic behind 20,193 parameters.

    No TensorFlow, so the CI job (NumPy only) and the smoke check can both pin
    the architecture.  ``padding`` is the free variable: the paper prints no
    padding, but only ``'same'`` reproduces its own parameter count, and
    ``'valid'`` lands on 17,121 -- that gap is the evidence for the choice, not
    a preference.
    """
    if padding not in ("same", "valid"):
        raise ValueError("padding must be 'same' or 'valid'")
    length = N_SAMPLES
    per_layer: dict[str, int] = {}
    previous = 1                                   # input channels
    for index, filters in enumerate(CONV_FILTERS, start=1):
        per_layer[f"conv{index}"] = filters * (previous * KERNEL_SIZE + 1)
        if padding == "valid":
            length -= KERNEL_SIZE - 1
        length //= POOL_SIZE                       # TensorFlow pools 'valid', stride 4
        previous = filters
    flat = length * previous
    per_layer["flatten_features"] = flat
    per_layer["fc"] = flat * DENSE_UNITS + DENSE_UNITS
    per_layer["out"] = DENSE_UNITS * len(CLASSES) + len(CLASSES)
    total = sum(per_layer[key] for key in per_layer if key != "flatten_features")
    return {**per_layer, "padding": padding, "total": total}


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """Read a PCM WAV as float64 mono in [-1, 1] (stdlib only, no soundfile)."""
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1:
            raise ValueError(f"{path}: expected mono, got {w.getnchannels()} ch")
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        fs = w.getframerate()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    return x, fs


def to_1khz(x: np.ndarray, fs: int) -> np.ndarray:
    """8 kHz -> 1 kHz.

    PAPER says only "downsampled to 1 kHz"; the method is E4.  ``resample_poly``
    with the default Kaiser window is an anti-aliased polyphase resampler, which
    is the choice that does not fold 1-4 kHz content back into the pass band.
    Alternatives rejected: ``x[::8]`` (aliases high-frequency noise into the
    murmur band) and ``scipy.signal.decimate`` (same filter, no advantage for
    an integer factor of 8).
    """
    if fs == FS_TARGET:
        return x.copy()
    if fs % FS_TARGET != 0:
        raise ValueError(f"fs={fs} is not an integer multiple of {FS_TARGET}")
    from scipy.signal import resample_poly

    return resample_poly(x, 1, fs // FS_TARGET)


def crop_or_pad(x: np.ndarray) -> tuple[np.ndarray, bool, bool]:
    """Force exactly N_SAMPLES, measured from the start of the recording.

    PAPER: "long and zero-padded short recordings were cropped to make all
    samples have an exact length of 2 s".  Crop position and pad side are E4:
    cropping from the start keeps the S1-aligned beginning the dataset authors
    edited in ("each recording contained three cycles"), and padding at the tail
    keeps the signal contiguous with zero instead of splitting it.  Measured on
    this copy: 950 cropped, 49 padded (shortest 1.16 s), 1 already exact.
    """
    cropped = padded = False
    if x.shape[0] > N_SAMPLES:
        x = x[:N_SAMPLES]
        cropped = True
    elif x.shape[0] < N_SAMPLES:
        x = np.pad(x, (0, N_SAMPLES - x.shape[0]))
        padded = True
    return x, cropped, padded


def min_max_scale(x: np.ndarray) -> np.ndarray:
    """Min-max to [-1, 1].

    PAPER: "The amplitude values of all samples were again scaled to the range
    between -1 and 1".  A constant clip cannot be rescaled; it would become
    zeros.  None is expected after cropping, and the count is reported (E4).
    """
    lo, hi = float(x.min()), float(x.max())
    if hi <= lo:
        return np.zeros_like(x)
    return (x - lo) / (hi - lo) * 2.0 - 1.0


@dataclass
class Clip:
    recording_id: str
    label: str
    label_index: int
    seconds_orig: float
    cropped: bool
    padded: bool


def load_yaseen(raw_dir: Path) -> tuple[np.ndarray, np.ndarray, list[Clip]]:
    """Load every Yaseen wav, apply the paper's pre-processing.

    Returns ``X`` float32 ``(1000, 2000, 1)`` (channels-last, what Keras
    Conv1D wants), ``y`` int64 ``(1000,)`` indexing ``CLASSES``, and per-clip
    metadata for the report.
    """
    clips: list[np.ndarray] = []
    rows: list[Clip] = []
    for label_index, label in enumerate(CLASSES):
        paths = sorted((raw_dir / label).glob("*.wav"))
        if not paths:
            raise SystemExit(f"no wav files under {raw_dir / label}")
        for path in paths:
            x, fs = read_wav_mono(path)
            seconds_orig = x.shape[0] / fs
            x = to_1khz(x, fs)
            x, cropped, padded = crop_or_pad(x)
            x = min_max_scale(x)
            clips.append(x)
            rows.append(
                Clip(
                    recording_id=path.stem,
                    label=label,
                    label_index=label_index,
                    seconds_orig=round(float(seconds_orig), 4),
                    cropped=cropped,
                    padded=padded,
                )
            )
    X = np.stack(clips).astype(np.float32)[..., None]
    y = np.array([r.label_index for r in rows], dtype=np.int64)
    return X, y, rows


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
def build_model(verbose: bool = False):
    """The paper's 20,193-parameter CNN.

    ``padding='same'`` is DERIVED, not assumed.  With ``valid`` convolutions the
    sequence stays one sample shorter per layer and the flattened vector is 100
    long, giving 17,121 parameters; with ``same`` it is 124 long and the total is
    exactly 20,193.  Max-pooling keeps TensorFlow's default ``valid`` stride-4
    (2000 -> 500 -> 125 -> 31) under both paddings, so the difference isolates
    the convolution padding alone.
    """
    import tensorflow as tf  # noqa: F401  (ensures a clear error if absent)
    from tensorflow import keras
    from tensorflow.keras import layers

    model = keras.Sequential(
        [
            keras.Input(shape=(N_SAMPLES, 1), name="pcg"),
            # PAPER: kernel 20, filters 16/8/4, tanh on every hidden layer.
            layers.Conv1D(CONV_FILTERS[0], KERNEL_SIZE, padding="same", activation="tanh", name="conv1"),
            layers.MaxPooling1D(POOL_SIZE, name="pool1"),
            layers.Conv1D(CONV_FILTERS[1], KERNEL_SIZE, padding="same", activation="tanh", name="conv2"),
            layers.MaxPooling1D(POOL_SIZE, name="pool2"),
            layers.Conv1D(CONV_FILTERS[2], KERNEL_SIZE, padding="same", activation="tanh", name="conv3"),
            layers.MaxPooling1D(POOL_SIZE, name="pool3"),
            layers.Flatten(name="flatten"),
            layers.Dense(DENSE_UNITS, activation="tanh", name="fc"),
            layers.Dense(len(CLASSES), activation="softmax", name="out"),
        ],
        name="ma2023_lightweight_cnn",
    )
    if verbose:
        model.summary()
    return model


def assert_parameter_count(model) -> dict:
    """Fail loudly if the layer configuration drifts from the paper."""
    total = int(model.count_params())
    if total != EXPECTED_PARAMS:
        raise SystemExit(
            f"parameter count is {total}, expected {EXPECTED_PARAMS}. "
            "The paper's own count pins padding='same' and 16/8/4/20/4/128/5; "
            "check the layer configuration before training anything."
        )
    per_layer = {layer.name: int(layer.count_params()) for layer in model.layers}
    # Independent recount of the same arithmetic: if the two disagree, the
    # TensorFlow build and the written architecture have drifted apart.
    analytic = parameter_breakdown("same")
    if analytic["total"] != total:
        raise SystemExit(
            f"TensorFlow reports {total} parameters but the analytic recount is "
            f"{analytic['total']}; the layer configuration is not the one the "
            "paper describes."
        )
    return {"total": total, "per_layer": per_layer, "analytic": analytic}


# --------------------------------------------------------------------------
# Folds
# --------------------------------------------------------------------------
def stratified_folds(y: np.ndarray, n_folds: int, seed: int) -> np.ndarray:
    """Fold index per clip: shuffle each class, deal round-robin.

    PAPER: "Ten-fold cross-validation ... commonly used in similar studies".
    It does not state stratification, but the dataset is exactly 200 per class
    and every comparable paper stratifies, so stratified round-robin is the
    faithful reading (E4 for the seed).  Because the paper's 2 s rule yields
    exactly one clip per recording, this split is *also* recording-disjoint:
    the window-leakage worry raised in ``docs/literature/17-*.md`` does not
    apply to the paper's own protocol.
    """
    rng = np.random.default_rng(seed)
    fold = np.empty(y.shape[0], dtype=np.int64)
    for label_index in np.unique(y):
        idx = np.flatnonzero(y == label_index)
        rng.shuffle(idx)
        fold[idx] = np.arange(idx.size) % n_folds
    return fold


def inner_val_mask(y_train: np.ndarray, seed: int, fraction: float) -> np.ndarray:
    """Stratified slice of the training folds, used only for early stopping.

    This applies the project's dev-set idea (ADR-031/037) to the reproduction:
    the test fold is never observed while the epoch is chosen.  The paper does
    not describe any early-stopping split (E4).
    """
    rng = np.random.default_rng(seed + 7_919)
    mask = np.zeros(y_train.shape[0], dtype=bool)
    for label_index in np.unique(y_train):
        idx = np.flatnonzero(y_train == label_index)
        rng.shuffle(idx)
        mask[idx[: max(1, int(round(fraction * idx.size)))]] = True
    return mask


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def confusion(y_true: np.ndarray, y_pred: np.ndarray, k: int) -> np.ndarray:
    cm = np.zeros((k, k), dtype=np.int64)
    for t, p in zip(y_true, y_pred):
        cm[int(t), int(p)] += 1
    return cm


def macro_f1(cm: np.ndarray) -> float:
    f1s = []
    for i in range(cm.shape[0]):
        tp = cm[i, i]
        fp = cm[:, i].sum() - tp
        fn = cm[i, :].sum() - tp
        denom = 2 * tp + fp + fn
        f1s.append(0.0 if denom == 0 else float(2 * tp / denom))
    return float(np.mean(f1s))


def t_ci95(values: np.ndarray) -> tuple[float, float]:
    """Student-t 95 % CI of the mean.

    The paper's CI is over 40 *attempts* (each attempt an aggregate of 10
    folds), so its width reflects run-to-run variance, not fold variance.
    Comparing a fold-level CI against it would compare different things; the
    report prints both and flags the difference.
    """
    values = np.asarray(values, dtype=np.float64)
    if values.size < 2:
        return float("nan"), float("nan")
    from scipy import stats

    mean = float(values.mean())
    se = float(values.std(ddof=1) / np.sqrt(values.size))
    half = float(stats.t.ppf(0.975, values.size - 1) * se)
    return mean - half, mean + half


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------
def set_determinism(tf, seed: int) -> None:
    """Seed Python, NumPy and TensorFlow so a fold can be re-run bit-for-bit.

    Without this the weight initialisation and the shuffle order are drawn from
    the global RNG state, so two runs of the "same" recipe silently differ and
    the reported number cannot be re-derived.  Each fold gets ``seed + k`` so the
    ten folds are independent of each other but reproducible individually.
    """
    import random

    random.seed(seed)
    np.random.seed(seed)
    tf.random.set_seed(seed)


def train_fold(X, y, train_mask, test_mask, seed, args) -> dict:
    import tensorflow as tf
    from tensorflow import keras

    set_determinism(tf, seed)

    X_tr_all, y_tr_all = X[train_mask], y[train_mask]
    val = inner_val_mask(y_tr_all, seed, args.val_fraction)

    Y_tr = keras.utils.to_categorical(y_tr_all, len(CLASSES))
    model = build_model()
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=args.lr),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )

    callbacks = []
    if args.early_stopping == "val":
        callbacks.append(
            keras.callbacks.EarlyStopping(
                monitor="val_loss",
                patience=args.patience,
                restore_best_weights=True,
                verbose=0,
            )
        )

    fit_kwargs = dict(
        epochs=args.epochs,
        batch_size=args.batch,
        verbose=0,
        callbacks=callbacks,
        shuffle=True,
    )
    if args.early_stopping == "val":
        fit_kwargs["validation_data"] = (X_tr_all[val], Y_tr[val])
        fit_X, fit_Y = X_tr_all[~val], Y_tr[~val]
    else:
        fit_X, fit_Y = X_tr_all, Y_tr

    started = time.time()
    history = model.fit(fit_X, fit_Y, **fit_kwargs)
    seconds = time.time() - started

    prob = model.predict(X[test_mask], verbose=0)
    pred = prob.argmax(axis=1)
    y_test = y[test_mask]
    acc = float((pred == y_test).mean())
    cm = confusion(y_test, pred, len(CLASSES))
    best_epoch = int(np.argmin(history.history["val_loss"]) + 1) if "val_loss" in history.history else int(len(history.history["loss"]))

    del model
    tf.keras.backend.clear_session()

    return {
        "n_train": int(train_mask.sum()),
        "n_inner_val": int(val.sum()),
        "n_test": int(test_mask.sum()),
        "epochs_run": int(len(history.history["loss"])),
        "best_epoch": best_epoch,
        "final_train_loss": float(history.history["loss"][-1]),
        "accuracy": acc,
        "macro_f1": macro_f1(cm),
        "confusion": cm.tolist(),
        "train_seconds": round(seconds, 2),
        "test_index": np.flatnonzero(test_mask).tolist(),
        "test_pred": pred.tolist(),
        "test_prob": np.round(prob, 6).tolist(),
    }


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def write_report(out_dir: Path, report: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repeats", type=int, default=1, help="PAPER used 40 attempts of the 10-fold run")
    parser.add_argument("--seed", type=int, default=42, help="fold seed for repeat 1 (E4)")
    parser.add_argument("--folds", type=int, default=N_FOLDS)
    parser.add_argument("--epochs", type=int, default=100, help="E4 upper bound")
    parser.add_argument("--batch", type=int, default=32, help="E4")
    parser.add_argument("--lr", type=float, default=1e-3, help="E4")
    parser.add_argument("--patience", type=int, default=10, help="E4 early-stopping patience")
    parser.add_argument("--val-fraction", type=float, default=0.10, help="E4 inner dev slice")
    parser.add_argument(
        "--early-stopping",
        choices=("val", "none"),
        default="val",
        help="'val' stops on the inner dev slice; 'none' trains the full epoch budget like the paper's curves",
    )
    parser.add_argument("--smoke", action="store_true", help="2 folds, 5 epochs: wiring check only")
    parser.add_argument("--save-final-model", type=Path, default=None, help="also train on all 1,000 clips and save a .keras model for the PWA export")
    parser.add_argument("--final-only", action="store_true",
                        help="skip the 10 folds and only train the all-data model (needs --save-final-model)")
    args = parser.parse_args(argv)

    if args.final_only and args.save_final_model is None:
        parser.error("--final-only needs --save-final-model")

    if args.smoke:
        args.folds = 2
        args.epochs = 5
        args.repeats = 1
        if args.out == DEFAULT_OUT:
            # Never let a wiring check overwrite the report of a real run.
            args.out = DEFAULT_OUT.with_name(DEFAULT_OUT.name + "-smoke")
        print("[smoke] 2 folds x 5 epochs -- the numbers are NOT comparable to the paper")

    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    import tensorflow as tf
    from tensorflow import keras

    print(f"tensorflow {tf.__version__} / keras {keras.__version__}")

    model = build_model()
    params = assert_parameter_count(model)
    del model
    keras.backend.clear_session()
    print(f"parameter self-check OK: {params['total']:,} == {EXPECTED_PARAMS:,}")

    X, y, clips = load_yaseen(args.raw_dir)
    n_cropped = sum(c.cropped for c in clips)
    n_padded = sum(c.padded for c in clips)
    per_class = {c: int((y == i).sum()) for i, c in enumerate(CLASSES)}
    print(f"clips {X.shape}, per class {per_class}, cropped {n_cropped}, padded {n_padded}")
    if X.shape[0] != 1000:
        raise SystemExit(f"expected 1,000 clips, got {X.shape[0]}")

    fold_of = stratified_folds(y, args.folds, args.seed)
    all_folds: list[dict] = []
    attempt_means: list[float] = []

    for repeat in range(0 if args.final_only else args.repeats):
        seed = args.seed + repeat * 1000
        if repeat:
            fold_of = stratified_folds(y, args.folds, seed)
        started = time.time()
        fold_accs = []
        for k in range(args.folds):
            test_mask = fold_of == k
            train_mask = ~test_mask
            res = train_fold(X, y, train_mask, test_mask, seed + k, args)
            res.update({"repeat": repeat, "fold": k})
            all_folds.append(res)
            fold_accs.append(res["accuracy"])
            print(
                f"[r{repeat} f{k}] acc={res['accuracy']*100:.2f}% "
                f"f1={res['macro_f1']*100:.2f}% epochs={res['epochs_run']} "
                f"({res['train_seconds']:.1f}s)"
            )
        attempt_mean = float(np.mean(fold_accs)) * 100.0
        attempt_means.append(attempt_mean)
        print(f"[repeat {repeat}] 10-fold mean accuracy = {attempt_mean:.3f}%  ({(time.time()-started)/60:.1f} min)")

    if args.final_only:
        # No folds were run: this mode exists only to produce the deployable
        # model, so there is no 10-fold report to write.
        all_acc = np.array([])
        fold_ci = (float("nan"), float("nan"))
        attempt_ci = (float("nan"), float("nan"))
    else:
        all_acc = np.array([f["accuracy"] for f in all_folds]) * 100.0
        fold_ci = t_ci95(all_acc)
        attempt_ci = t_ci95(np.array(attempt_means))

    has_folds = all_acc.size > 0
    mean_acc = float(all_acc.mean()) if has_folds else float("nan")

    report = {
        "reference": {
            "paper": "Ma S., Chen J., Ho J.W.K., Comput. Methods Programs Biomed. 243 (2024) 107906",
            "doi": "10.1016/j.cmpb.2023.107906",
            "reported_accuracy_pct": PAPER_ACC,
            "reported_ci95_pct": list(PAPER_CI),
            "reported_attempts": PAPER_ATTEMPTS,
            "acceptance_band_pct": [PAPER_ACC - 1.5, PAPER_ACC + 1.5],
        },
        "self_check": {
            "parameters": params,
            "n_clips": int(X.shape[0]),
            "n_per_class": per_class,
            "n_cropped_to_2s": n_cropped,
            "n_zero_padded": n_padded,
            "input_shape": list(X.shape[1:]),
        },
        "protocol": {
            "sample_rate_hz": FS_TARGET,
            "n_samples": N_SAMPLES,
            "normalisation": "per-clip min-max to [-1, 1]",
            "band_pass": "none (the paper does not band-pass Yaseen)",
            "n_folds": args.folds,
            "folds": "stratified round-robin over clips, one clip per recording => recording-disjoint",
            "attempts_run": args.repeats,
            "optimiser": "Adam",
            "loss": "categorical cross-entropy",
            "activation": "tanh",
            "batch_size": args.batch,
            "learning_rate": args.lr,
            "max_epochs": args.epochs,
            "early_stopping": args.early_stopping,
            "early_stopping_patience": args.patience,
            "inner_val_fraction": args.val_fraction,
            "seed": args.seed,
            "smoke": args.smoke,
        },
        "deviations": [
            "learning rate 1e-3: not stated by the paper (E4)",
            "training batch size 32: not stated by the paper (E4); the paper's 'batch size equal to 2' is the on-device fine-tuning demo",
            "epoch budget 100 + early stopping on an inner 10 % dev slice: the paper reports curves up to 100 epochs but no stopping rule (E4)",
            "resampling 8k->1k via scipy resample_poly: method not stated (E4)",
            "crop from the start / zero-pad at the tail: position not stated (E4)",
            "stratified folds and fold seed: the paper says only '10-fold' (E4)",
            "attempts: the paper averages 40 repeats of the 10-fold run; this run uses the number given by --repeats",
        ],
        "results": {
            "fold_accuracy_pct": [round(a, 4) for a in all_acc],
            "fold_accuracy_mean_pct": round(mean_acc, 4) if has_folds else None,
            "fold_accuracy_std_pct": round(float(all_acc.std(ddof=1)), 4) if all_acc.size > 1 else None,
            "fold_accuracy_ci95_pct": [round(x, 4) for x in fold_ci],
            "attempt_mean_accuracy_pct": [round(a, 4) for a in attempt_means],
            "attempt_mean_ci95_pct": [round(x, 4) for x in attempt_ci],
            "macro_f1_mean_pct": round(float(np.mean([f["macro_f1"] for f in all_folds]) * 100), 4) if has_folds else None,
            "delta_vs_paper_pct": round(mean_acc - PAPER_ACC, 4) if has_folds else None,
            "within_acceptance_band": bool(abs(mean_acc - PAPER_ACC) <= 1.5) if has_folds else None,
        },
        "folds": all_folds,
        "clips": [asdict(c) for c in clips],
    }
    if not args.final_only:
        write_report(args.out, report)
        print(f"report -> {args.out / 'report.json'}")

    if args.save_final_model is not None:
        # Same recipe, trained on all 1,000 clips, for the TF.js/PWA path.
        Y = keras.utils.to_categorical(y, len(CLASSES))
        val = inner_val_mask(y, args.seed, args.val_fraction)
        final = build_model()
        final.compile(
            optimizer=keras.optimizers.Adam(learning_rate=args.lr),
            loss="categorical_crossentropy",
            metrics=["accuracy"],
        )
        cbs = []
        if args.early_stopping == "val":
            cbs.append(
                keras.callbacks.EarlyStopping(
                    monitor="val_loss", patience=args.patience, restore_best_weights=True, verbose=0
                )
            )
        if args.early_stopping == "val":
            final.fit(X[~val], Y[~val], validation_data=(X[val], Y[val]), epochs=args.epochs,
                      batch_size=args.batch, verbose=0, callbacks=cbs, shuffle=True)
        else:
            final.fit(X, Y, epochs=args.epochs, batch_size=args.batch, verbose=0, callbacks=cbs, shuffle=True)
        args.save_final_model.parent.mkdir(parents=True, exist_ok=True)
        final.save(args.save_final_model)
        print(f"final model -> {args.save_final_model}")

    if not args.final_only:
        print(
            f"\nmean 10-fold accuracy = {all_acc.mean():.3f}% "
            f"(fold CI95 {fold_ci[0]:.3f}-{fold_ci[1]:.3f}), "
            f"delta vs paper = {all_acc.mean() - PAPER_ACC:+.3f} pt, "
            f"acceptance {'PASS' if abs(all_acc.mean() - PAPER_ACC) <= 1.5 else 'FAIL'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
