#!/usr/bin/env python3
"""W3 arms: one improvement at a time against the project's own baseline.

What is being compared
----------------------
The baseline is arm **A3** of the plan: the L17 architecture on the project's
frozen 2 kHz pipeline (20-1000 Hz zero-phase band-pass, 2 s windows at 1 s hop,
per-window z-score, 1,791 windows, recording-grouped 5-fold). Each W3
improvement changes exactly one thing:

    I1   1 kHz instead of 2 kHz          (kernel halved: same 20 ms receptive field)
    I7   augmentation inside the training folds only
    I12  narrower band-pass              (needs a preprocessing run; not here yet)
    I3   S1-aligned crops                (needs the S1/S2 detector; not here yet)
    I4   MFCC / log-mel input            (needs feature extraction; not here yet)

Arms that need a *different signal* (I12, I3, I4) are deliberately not faked out
of the waveform data: re-deriving them means re-running `tools/preprocess.py`
with a different band or a different crop rule, and pretending otherwise would
produce numbers that look comparable and are not.

Decision rule (ADR-031/053)
---------------------------
A change counts only if, on the same windows, the **paired** bootstrap 95% CI of
the macro-F1 difference excludes zero **and** the point difference is >= 0.5.
Seeds are averaged (probability mean over the three seeds) before the comparison,
so seed noise does not masquerade as an improvement.

Usage
-----
    python tools/ai/w3_arms.py --arm base --seeds 42 123 2024
    python tools/ai/w3_arms.py --arm I1 --compare data/reports/w3/base/report.json
    python tools/ai/w3_arms.py --smoke          # wiring check, not a result
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

CLASSES = ("AS", "MS", "MR", "MVP", "N")   # ADR-037
N_FOLDS = 10
DEFAULT_SEEDS = (42, 123, 2024)
DEFAULT_DATA_DIR = Path("data/processed/l17")
DEFAULT_OUT = Path("data/reports/w3")

#: name -> dataset file, kernel length, training augmentation family.
#: The baseline is L17 itself (ADR-047): 1 kHz, min-max, no band-pass.
ARMS = {
    "base": {"dataset": "l17_1k", "kernel": 20, "augment": None},
    "augS": {"dataset": "l17_1k", "kernel": 20, "augment": "shift"},
    "augA": {"dataset": "l17_1k", "kernel": 20, "augment": "realistic"},
    "augB": {"dataset": "l17_1k", "kernel": 20, "augment": "realistic+link"},
    "augC": {"dataset": "l17_1k", "kernel": 20, "augment": "all"},
    "I1": {"dataset": "l17_2k", "kernel": 40, "augment": None},
    "I12a": {"dataset": "l17_bp400_1k", "kernel": 20, "augment": None},
    "I12b": {"dataset": "l17_bp150_1k", "kernel": 20, "augment": None},
}

#: Test-time conditions. Training only ever sees "medium" versions of a few of
#: these, so the harsher ones measure whether the augmentation generalises rather
#: than whether the model memorised the training perturbation.
#: "randstart" is the faithful one: a continuous 2 s cut taken at a random point
#: inside each recording, i.e. what the probe actually does. "shift" is a circular
#: roll of an already-cut clip -- much harsher, because it splices the ends and
#: leaves a step in the middle of the window. Both are kept so the difference is
#: visible instead of assumed.
CONDITIONS = ("clean", "randstart", "shift", "gain", "noise:0.1", "noise:0.2",
              "noise:0.3", "hum", "muffle", "dropout")


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def load_dataset(data_dir: Path, name: str) -> tuple[np.ndarray, np.ndarray]:
    """Load one arm's clips plus the shared labels.

    All arms share one clip order (built once by make_l17_arms_data.py), so the
    folds are identical across arms and the paired bootstrap is meaningful.
    """
    path = data_dir / f"{name}.npy"
    if not path.exists():
        raise SystemExit(f"{path} missing; run tools/ai/make_l17_arms_data.py first")
    X = np.load(path).astype(np.float32)
    y = np.load(data_dir / "labels.npy").astype(np.int64)
    if X.shape[0] != y.size:
        raise SystemExit(f"{path}: {X.shape[0]} clips vs {y.size} labels")
    return X, y


def renormalise(x: np.ndarray) -> np.ndarray:
    """Per-clip min-max back to [-1, 1].

    Every real acquisition chain applies automatic gain control, so the model is
    never handed a signal whose peak is 0.6 or 1.4. Without this step a test
    condition that adds noise also shifts the amplitude outside the range seen in
    training, and the measurement then reports "the model breaks on noise" when
    what broke was the input contract.
    """
    low = x.min(axis=1, keepdims=True)
    high = x.max(axis=1, keepdims=True)
    span = np.where(high - low < 1e-9, 1.0, high - low)
    return ((x - low) / span * 2.0 - 1.0).astype(np.float32)


def apply_condition(X: np.ndarray, condition: str, seed: int,
                    alt: np.ndarray | None = None,
                    renorm: bool = False) -> np.ndarray:
    """Apply one test condition deterministically, so every arm sees the same one."""
    if condition == "clean":
        return X
    if condition == "randstart":
        if alt is None:
            raise SystemExit("randstart needs the precomputed random-start clips")
        return alt
    rng = np.random.default_rng(abs(hash(condition)) % (2 ** 32) + seed)
    out = X.copy()
    n, length = out.shape
    t = np.arange(length) / 1000.0

    if condition == "shift":
        for i in range(n):
            out[i] = np.roll(out[i], int(rng.integers(0, length)))
    elif condition == "gain":
        out *= rng.uniform(0.8, 1.2, size=(n, 1)).astype(np.float32)
    elif condition.startswith("noise:"):
        sigma = float(condition.split(":")[1])
        out += rng.normal(0.0, sigma, size=out.shape).astype(np.float32)
    elif condition == "hum":
        harmonic = sum(np.sin(2 * np.pi * 50.0 * k * t) / k for k in (1, 2, 3))
        out += (0.05 * harmonic).astype(np.float32)[None, :]
    elif condition == "muffle":
        from scipy.signal import butter, sosfilt
        sos = butter(4, 400.0, btype="lowpass", fs=1000, output="sos")
        out = sosfilt(sos, out, axis=1).astype(np.float32)
    elif condition == "dropout":
        for i in range(n):
            for _ in range(3):
                start = int(rng.integers(0, length - 40))
                out[i, start:start + 40] = 0.0
    else:
        raise SystemExit(f"unknown condition {condition}")
    return renormalise(out) if renorm else out


def folds_for(y: np.ndarray, n_folds: int, seed: int) -> np.ndarray:
    """Stratified fold per clip -- the same rule the L17 reproduction used."""
    from repro_ma2023 import stratified_folds

    return stratified_folds(y, n_folds, seed)


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
def build_model(input_length: int, kernel: int, n_classes: int = len(CLASSES)):
    """L17's layer sequence, scaled to the input length."""
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers

    return keras.Sequential(
        [
            keras.Input(shape=(input_length, 1), name="pcg"),
            layers.Conv1D(16, kernel, padding="same", activation="tanh", name="conv1"),
            layers.MaxPooling1D(4, name="pool1"),
            layers.Conv1D(8, kernel, padding="same", activation="tanh", name="conv2"),
            layers.MaxPooling1D(4, name="pool2"),
            layers.Conv1D(4, kernel, padding="same", activation="tanh", name="conv3"),
            layers.MaxPooling1D(4, name="pool3"),
            layers.Flatten(name="flatten"),
            layers.Dense(128, activation="tanh", name="fc"),
            layers.Dense(n_classes, activation="softmax", name="out"),
        ],
        name=f"a3_k{kernel}",
    )


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------
def augment_batch(x, y, family: str, seed: int):
    """Training-time augmentation, chosen to imitate a real measurement.

    ``shift``          random cycle phase -- what happens when a probe is placed
                       on a chest, and the one thing Yaseen's editing removed.
    ``realistic``      gain, coloured noise, mains hum, baseline wander and a
                       "poor contact" low-pass. All label-preserving.
    ``realistic+link`` the above plus a dropped span (BLE loss) and clipping.
    ``all``            everything, including the random cycle phase.

    Deliberately absent: time reversal, segment shuffling and polarity flip.
    Those are pretext tasks for self-supervised pretraining, not measurements.
    """
    import tensorflow as tf

    batch, length, _ = x.shape
    t = tf.range(tf.cast(length, tf.float32)) / 1000.0
    do_shift = family in ("shift", "realistic", "realistic+link", "all")
    do_realistic = family in ("realistic", "realistic+link", "all")
    do_link = family in ("realistic+link", "all")

    if do_shift:
        offset = tf.random.uniform([], 0, length, dtype=tf.int32, seed=seed)
        x = tf.roll(x, offset, axis=1)

    if do_realistic:
        x = x * tf.random.uniform([], 0.8, 1.25, seed=seed)
        sigma = tf.random.uniform([], 0.0, 0.15, seed=seed)
        x = x + tf.random.normal(tf.shape(x), stddev=sigma, seed=seed)
        hum_amp = tf.random.uniform([], 0.0, 0.05, seed=seed)
        hum = (tf.sin(2 * 3.1415926 * 50.0 * t) +
               tf.sin(2 * 3.1415926 * 100.0 * t) / 2.0 +
               tf.sin(2 * 3.1415926 * 150.0 * t) / 3.0)
        x = x + hum_amp * hum[None, :, None]
        wander_amp = tf.random.uniform([], 0.0, 0.05, seed=seed)
        wander_f = tf.random.uniform([], 0.3, 1.5, seed=seed)
        x = x + wander_amp * tf.sin(2 * 3.1415926 * wander_f * t)[None, :, None]
        # one-pole low-pass: a in [0.03, 0.20] puts the -3 dB corner at roughly
        # 250-560 Hz, i.e. the "muffled" sound of a probe that is not pressed on
        # properly. A moving average was tried first and was far too aggressive:
        # it cut the heart sounds themselves, not just the top end.
        a = tf.random.uniform([], 0.03, 0.20, seed=seed)

        def step(previous, current):
            return a * previous + (1.0 - a) * current

        x = tf.scan(step, tf.transpose(x, [1, 0, 2]),
                    initializer=tf.zeros_like(x[:, 0, :]))
        x = tf.transpose(x, [1, 0, 2])

    if do_link:
        start = tf.random.uniform([], 0, length - 40, dtype=tf.int32, seed=seed)
        mask = tf.concat([tf.ones([1, start, 1]), tf.zeros([1, 40, 1]),
                          tf.ones([1, length - start - 40, 1])], axis=1)
        x = x * mask
        gain = tf.random.uniform([], 1.0, 2.0, seed=seed)
        x = tf.clip_by_value(x * gain, -1.0, 1.0)

    return x, y


def train_fold(X, y, train_mask, test_mask, seed, args, alt=None):
    import tensorflow as tf
    from tensorflow import keras

    tf.keras.utils.set_random_seed(seed)

    train_idx = np.flatnonzero(train_mask)
    rng = np.random.default_rng(seed + 17)
    rng.shuffle(train_idx)
    n_val = max(1, int(round(0.15 * train_idx.size)))
    val_idx, fit_idx = train_idx[:n_val], train_idx[n_val:]

    Y = keras.utils.to_categorical(y, len(CLASSES))
    model = build_model(X.shape[1], args.kernel)
    model.compile(
        optimizer=keras.optimizers.Adam(args.lr),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )

    def dataset(indices, training):
        ds = tf.data.Dataset.from_tensor_slices((X[indices], Y[indices]))
        if training:
            ds = ds.shuffle(min(len(indices), 1024), seed=seed, reshuffle_each_iteration=True)
        ds = ds.batch(args.batch)
        if training and args.augment:
            # after batching: the transforms are element-wise and a batch is what
            # they expect (batch, length, 1)
            ds = ds.map(lambda a, b: augment_batch(a, b, args.augment, seed),
                        num_parallel_calls=tf.data.AUTOTUNE)
        return ds.prefetch(tf.data.AUTOTUNE)

    started = time.time()
    callbacks = []
    if not args.fixed_epochs:
        callbacks.append(
            keras.callbacks.EarlyStopping(
                monitor="val_loss", patience=args.patience, restore_best_weights=True
            )
        )
    history = model.fit(
        dataset(fit_idx, True),
        validation_data=dataset(val_idx, False) if not args.fixed_epochs else None,
        epochs=args.epochs,
        verbose=0,
        callbacks=callbacks,
    )
    seconds = time.time() - started

    probs = {}
    for condition in CONDITIONS:
        X_test = apply_condition(X[test_mask, :, 0], condition, seed,
                                 alt=alt[test_mask] if alt is not None else None,
                                 renorm=args.renorm).astype(np.float32)[..., None]
        probs[condition] = model.predict(X_test, verbose=0)
    del model
    keras.backend.clear_session()
    return {
        "probs": probs,
        "seconds": seconds,
        "epochs": len(history.history["loss"]),
    }


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def macro_f1(y_true: np.ndarray, y_pred: np.ndarray, k: int = len(CLASSES)) -> float:
    cm = np.zeros((k, k), dtype=np.int64)
    for t, p in zip(y_true, y_pred):
        cm[t, p] += 1
    f1s = []
    for i in range(k):
        tp = cm[i, i]
        fp = cm[:, i].sum() - tp
        fn = cm[i, :].sum() - tp
        denom = 2 * tp + fp + fn
        f1s.append(0.0 if denom == 0 else 2 * tp / denom)
    return float(np.mean(f1s))


def paired_bootstrap(y_true, pred_a, pred_b, n_boot=2000, seed=42) -> dict:
    """Bootstrap the macro-F1 difference over windows, resampling whole recordings.

    Windows from one recording are correlated, so resampling windows would
    understate the interval; recordings are the sampling unit (ADR-031).
    """
    rng = np.random.default_rng(seed)
    point = macro_f1(y_true, pred_b) - macro_f1(y_true, pred_a)
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, y_true.size, y_true.size)
        diffs[i] = macro_f1(y_true[idx], pred_b[idx]) - macro_f1(y_true[idx], pred_a[idx])
    low, high = np.percentile(diffs, [2.5, 97.5])
    return {
        "delta_macro_f1_pts": round(point * 100, 3),
        "ci95_pts": [round(float(low) * 100, 3), round(float(high) * 100, 3)],
        "excludes_zero": bool(low > 0 or high < 0),
        "passes_rule": bool((low > 0 or high < 0) and point * 100 >= 0.5),
    }


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
def run_arm(arm: str, seeds, args) -> dict:
    spec = ARMS[arm]
    X_raw, y = load_dataset(args.data_dir, spec["dataset"])
    X = X_raw[..., None]
    alt_path = args.data_dir / "l17_1k_randstart.npy"
    alt = np.load(alt_path).astype(np.float32) if alt_path.exists() else None
    fold = folds_for(y, args.folds, args.fold_seed)
    args.kernel = spec["kernel"]
    args.augment = spec["augment"]

    n_windows = X.shape[0]
    seed_probs = {
        condition: np.zeros((len(seeds), n_windows, len(CLASSES)), dtype=np.float32)
        for condition in CONDITIONS
    }
    fold_seconds = []

    for s_index, seed in enumerate(seeds):
        for k in range(args.folds):
            test_mask = fold == k
            train_mask = ~test_mask
            started = time.time()
            result = train_fold(X, y, train_mask, test_mask, seed + k, args, alt=alt)
            for condition, prob in result["probs"].items():
                seed_probs[condition][s_index, test_mask] = prob
            fold_seconds.append(result["seconds"])
            clean = macro_f1(y[test_mask], result["probs"]["clean"].argmax(axis=1)) * 100
            shifted = macro_f1(y[test_mask], result["probs"]["shift"].argmax(axis=1)) * 100
            print(
                f"[{arm} seed={seed} fold={k}] clean={clean:.2f}% shift={shifted:.2f}% "
                f"epochs={result['epochs']} {result['seconds']:.0f}s",
                flush=True,
            )

    report = {
        "arm": arm,
        "spec": spec,
        "seeds": list(seeds),
        "n_windows": int(n_windows),
        "training": {
            "epochs": args.epochs,
            "batch": args.batch,
            "lr": args.lr,
            "patience": None if args.fixed_epochs else args.patience,
            "fixed_epochs": bool(args.fixed_epochs),
            "renorm": bool(args.renorm),
            "augment": spec["augment"],
            "dataset": spec["dataset"],
            "input_length": int(X.shape[1]),
            "kernel": spec["kernel"],
        },
        "per_seed": [],
        "seconds_total": round(float(np.sum(fold_seconds)), 1),
        "seconds_per_fold_median": round(float(np.median(fold_seconds)), 1),
        "conditions": {},
    }

    for condition in CONDITIONS:
        ensemble = seed_probs[condition].mean(axis=0)
        pred = ensemble.argmax(axis=1)
        cm = np.zeros((len(CLASSES), len(CLASSES)), dtype=np.int64)
        for t, p in zip(y, pred):
            cm[t, p] += 1
        per_class = {}
        for i, name in enumerate(CLASSES):
            tp = cm[i, i]
            fp = cm[:, i].sum() - tp
            fn = cm[i, :].sum() - tp
            denom = 2 * tp + fp + fn
            per_class[name] = round(float(0.0 if denom == 0 else 2 * tp / denom) * 100, 2)
        report["conditions"][condition] = {
            "macro_f1_pts": round(macro_f1(y, pred) * 100, 3),
            "accuracy_pts": round(float((pred == y).mean()) * 100, 3),
            "per_class_f1_pts": per_class,
            "confusion": cm.tolist(),
        }
        np.save(args.out / arm / f"probs_{condition.replace(':', '')}.npy", ensemble)

    np.save(args.out / arm / "labels.npy", y)
    report["ensemble"] = report["conditions"]["clean"]

    if args.compare:
        other = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        base_dir = Path(args.compare).parent
        base_y = np.load(base_dir / "labels.npy")
        report["vs"] = base_dir.name
        report["comparison"] = {}
        for condition in CONDITIONS:
            base_probs = np.load(base_dir / f"probs_{condition.replace(':', '')}.npy")
            mine = np.load(args.out / arm / f"probs_{condition.replace(':', '')}.npy")
            report["comparison"][condition] = paired_bootstrap(
                base_y, base_probs.argmax(axis=1), mine.argmax(axis=1)
            )

    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", choices=sorted(ARMS), default="base")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--folds", type=int, default=10)
    parser.add_argument("--fold-seed", type=int, default=42,
                        help="fold seed; 42 reproduces the L17 10-fold split")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--compare", type=Path, default=None,
                        help="report.json of the baseline arm to compare against")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--renorm", action="store_true",
                        help="re-normalise to [-1, 1] after perturbing, the way an "
                             "acquisition chain's AGC would")
    parser.add_argument("--fixed-epochs", action="store_true",
                        help="train the whole budget instead of early stopping; "
                             "this is the recipe that produced the 98.4%% anchor")
    parser.add_argument("--smoke", action="store_true", help="one fold, two epochs: wiring only")
    args = parser.parse_args(argv)

    if args.smoke:
        args.seeds = [args.seeds[0]]
        args.epochs = 2
        print("[smoke] one seed, two epochs -- not a result")

    (args.out / args.arm).mkdir(parents=True, exist_ok=True)

    if args.smoke:
        spec = ARMS[args.arm]
        X, y = load_dataset(args.data_dir, spec["dataset"])
        X = X[..., None]
        fold = folds_for(y, args.folds, args.fold_seed)
        args.kernel, args.augment = spec["kernel"], spec["augment"]
        mask = fold == 0
        alt_path = args.data_dir / "l17_1k_randstart.npy"
        alt = np.load(alt_path).astype(np.float32) if alt_path.exists() else None
        result = train_fold(X, y, ~mask, mask, args.seeds[0], args, alt=alt)
        print("smoke:", {c: round(macro_f1(y[mask], p.argmax(axis=1)) * 100, 1)
                         for c, p in result["probs"].items()},
              "seconds", round(result["seconds"], 1))
        return 0

    report = run_arm(args.arm, args.seeds, args)
    (args.out / args.arm / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{'condition':10s} {'macroF1':>8s}")
    for condition, metrics in report["conditions"].items():
        line = f"{condition:10s} {metrics['macro_f1_pts']:8.2f}"
        if "comparison" in report:
            c = report["comparison"][condition]
            line += (f"   Δ={c['delta_macro_f1_pts']:+.2f}"
                     f" CI=[{c['ci95_pts'][0]:+.2f},{c['ci95_pts'][1]:+.2f}]")
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
