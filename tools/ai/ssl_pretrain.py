#!/usr/bin/env python3
"""Upstream self-supervised pre-training, following Ma et al. 2023 section 2.3.

Why this file exists
--------------------
The paper's robustness does not come from augmentation alone. Its §3.2 reports
91.5 -> 96.1 on perturbed data, and the mechanism is a six-task pretext stage:
an upstream network learns to recognise *what was done to a recording*, and its
convolutional layers are then transferred to the classifier with the first one
frozen. Without that stage we are not comparing like with like.

The six transformations (§2.3), each applied to a sample with probability 0.5:

    1 noise        Gaussian, sigma = 0.2, same length
    2 scaling      amplitude x 0.8
    3 negation     amplitude sign flipped
    4 flip         time reversed
    5 permutation  10 segments reconnected in random order
    6 stretch      10 segments, each stretched or squeezed

Each sample therefore carries six binary pseudo-labels: 1 if that transformation
was applied, 0 if not. The upstream model is the downstream convolutional stack
followed by six independent branches, and its loss is the sum of the six binary
cross-entropies.

Unlabelled data
---------------
The paper uses ~25,000 interpretable clips from four PhysioNet/CinC 2016
databases with the labels discarded. This machine has 11,728 two-second windows
of CirCor (also PhysioNet), 4 kHz, which are decimated to 1 kHz and min-max
scaled. CirCor is not the classification target, so it cannot leak into the
Yaseen task; the role it plays is the same as the paper's unlabelled pool.

Usage
-----
    python tools/ai/ssl_pretrain.py --epochs 6 --limit 8000
    python tools/ai/ssl_pretrain.py --check        # verify the transforms only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

CLASSES = ("AS", "MS", "MR", "MVP", "N")
TASKS = ("noise", "scaling", "negation", "flip", "permutation", "stretch")
DEFAULT_UNLABELLED = Path("data/processed/ssl/unlabelled_1k.npy")
DEFAULT_OUT = Path("data/models/ssl")


def transform(x: np.ndarray, task: str, rng: np.random.Generator) -> np.ndarray:
    """One pretext transformation. Uses the paper's parameters where given."""
    if task == "noise":
        return x + rng.normal(0.0, 0.2, size=x.shape).astype(np.float32)
    if task == "scaling":
        return (x * 0.8).astype(np.float32)
    if task == "negation":
        return (-x).astype(np.float32)
    if task == "flip":
        return x[::-1].copy()
    if task in ("permutation", "stretch"):
        segments = np.array_split(x, 10)
        if task == "permutation":
            order = rng.permutation(10)
            return np.concatenate([segments[i] for i in order]).astype(np.float32)
        out = []
        for segment in segments:
            factor = rng.uniform(0.7, 1.4)
            target = max(2, int(round(segment.size * factor)))
            index = np.linspace(0, segment.size - 1, target)
            out.append(np.interp(index, np.arange(segment.size), segment).astype(np.float32))
        stretched = np.concatenate(out)
        # the concatenation changes the length; bring it back to the input length
        index = np.linspace(0, stretched.size - 1, x.size)
        return np.interp(index, np.arange(stretched.size), stretched).astype(np.float32)
    raise SystemExit(f"unknown task {task}")


def make_transformed(X: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Apply each task with probability 0.5 and return (inputs, six binary labels).

    Vectorised over the corpus: the first version called `transform` once per
    sample per task (150k Python calls per epoch) and took 250 s an epoch, which
    made "just train it longer" expensive enough to discourage the experiment
    that actually matters.
    """
    rng = np.random.default_rng(seed)
    out = X.copy()
    n, length = out.shape
    labels = np.zeros((n, len(TASKS)), dtype=np.float32)

    for index, task in enumerate(TASKS):
        chosen = np.flatnonzero(rng.random(n) < 0.5)
        labels[chosen, index] = 1.0
        if chosen.size == 0:
            continue
        block = out[chosen]

        if task == "noise":
            out[chosen] = block + rng.normal(0.0, 0.2, size=block.shape).astype(np.float32)
        elif task == "scaling":
            out[chosen] = block * 0.8
        elif task == "negation":
            out[chosen] = -block
        elif task == "flip":
            out[chosen] = block[:, ::-1]
        elif task == "permutation":
            segments = np.array_split(np.arange(length), 10)
            for row, i in enumerate(chosen):
                order = rng.permutation(10)
                out[i] = np.concatenate([block[row, segments[k]] for k in order])
        elif task == "stretch":
            # one random factor per row, applied through a gather index
            factors = rng.uniform(0.7, 1.4, size=chosen.size)
            base = np.arange(length, dtype=np.float32) * factors[:, None]
            gather = np.clip(base, 0, length - 1).astype(np.int32)
            out[chosen] = np.take_along_axis(block, gather, axis=1)
    return out, labels


def build_upstream(input_length: int, kernel: int = 20, n_tasks: int = len(TASKS)):
    """Three conv-maxpool layers, then one branch per task (two layers deeper)."""
    from tensorflow import keras
    from tensorflow.keras import layers

    inputs = keras.Input(shape=(input_length, 1), name="pcg")
    x = layers.Conv1D(16, kernel, padding="same", activation="tanh", name="conv1")(inputs)
    x = layers.MaxPooling1D(4, name="pool1")(x)
    x = layers.Conv1D(8, kernel, padding="same", activation="tanh", name="conv2")(x)
    x = layers.MaxPooling1D(4, name="pool2")(x)
    x = layers.Conv1D(4, kernel, padding="same", activation="tanh", name="conv3")(x)
    x = layers.MaxPooling1D(4, name="pool3")(x)
    features = layers.Flatten(name="flatten")(x)

    # Four Dense layers per branch: Fig. 1a shows the downstream section as two
    # Dense layers (128 then 5), and the text says each branch is "two layers
    # deeper", so 4 = 2 + 2. An earlier 3-layer version came from misreading the
    # text alone; the figure settles it.
    outputs = []
    for task in TASKS:
        branch = layers.Dense(128, activation="tanh", name=f"{task}_fc1")(features)
        branch = layers.Dense(64, activation="tanh", name=f"{task}_fc2")(branch)
        branch = layers.Dense(32, activation="tanh", name=f"{task}_fc3")(branch)
        outputs.append(layers.Dense(2, activation="softmax", name=f"{task}_out")(branch))
    return keras.Model(inputs, outputs, name="ssl_upstream")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--unlabelled", type=Path, default=DEFAULT_UNLABELLED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--limit", type=int, default=0, help="use only the first N clips")
    parser.add_argument("--transform-seed", type=int, default=20261109)
    parser.add_argument("--init-from", type=Path, default=None,
                        help="resume from a saved upstream model")
    parser.add_argument("--check", action="store_true", help="verify the transforms, then stop")
    args = parser.parse_args(argv)

    import tensorflow as tf

    X = np.load(args.unlabelled).astype(np.float32)
    if args.limit:
        X = X[: args.limit]
    if X.ndim == 2:
        X = X[..., None]
    print(f"unlabelled clips: {X.shape[0]:,} x {X.shape[1]} samples")

    if args.check:
        sample = X[0, :, 0]
        rng = np.random.default_rng(7)
        for task in TASKS:
            changed = transform(sample, task, rng)
            print(f"  {task:12s} length={changed.size:5d} "
                  f"changed={not np.allclose(sample, changed)} "
                  f"same_length={changed.size == sample.size}")
        transformed, labels = make_transformed(X[:200, :, 0], seed=7)
        print("labelled fraction per task:", labels.mean(axis=0).round(2).tolist())
        return 0

    model = build_upstream(X.shape[1])
    # six independent binary tasks; the paper sums their losses, which is what
    # Keras does by default when a list of losses is given without weights
    model.compile(
        optimizer=tf.keras.optimizers.Adam(args.lr),
        loss="categorical_crossentropy",
        metrics=[["accuracy"] for _ in TASKS],
    )
    print("upstream parameters:", f"{model.count_params():,}")

    # Algorithm 1 lines 1-11: the transformed dataset XT and the pseudo-labels YP
    # are built ONCE, before training, and the training loop (lines 13-15) reuses
    # them. An earlier version redrew the transformations every epoch, which is a
    # different experiment: the same clip then appears with different labels from
    # one epoch to the next, so the tasks are never consistently defined.
    started = time.time()
    transformed, pseudo = make_transformed(X[..., 0], seed=args.transform_seed)
    targets = [tf.keras.utils.to_categorical(pseudo[:, i], 2) for i in range(len(TASKS))]
    print(f"transformed set built once in {time.time() - started:.0f}s; "
          f"label rates {pseudo.mean(axis=0).round(2).tolist()}", flush=True)

    history = model.fit(
        transformed[..., None], targets,
        epochs=args.epochs, batch_size=args.batch, verbose=2,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    model.save(args.out / "upstream.keras")
    conv = {f"conv{i}": model.get_layer(f"conv{i}").get_weights() for i in (1, 2, 3)}
    np.savez(args.out / "conv_weights.npz",
             **{f"{k}_{j}": w for k, weights in conv.items() for j, w in enumerate(weights)})
    (args.out / "summary.json").write_text(json.dumps({
        "clips": int(X.shape[0]),
        "epochs": args.epochs,
        "tasks": list(TASKS),
        "seconds": round(time.time() - started, 1),
        "source": str(args.unlabelled),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("saved ->", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
