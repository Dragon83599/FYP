#!/usr/bin/env python3
"""Export the reproduction model to the TensorFlow.js *layers-model* format.

Why this file exists instead of ``tensorflowjs_converter``
----------------------------------------------------------
The official converter cannot be installed in this environment without dragging
in the whole JAX/Flax/TensorFlow-Decision-Forests tree (``tensorflowjs`` 4.22
requires ``jax``, ``jaxlib``, ``flax``, ``tf-keras``, ``tensorflow``,
``tensorflow-decision-forests``; ``tensorflow_decision_forests`` in turn needs
``yggdrasil_decision_forests``, which is not on the mirror at all).  For a
layers model — a ``Sequential`` of Conv1D / MaxPooling1D / Flatten / Dense —
the on-disk format is small and fully documented, so this script writes it
directly:

* ``model.json`` — topology plus a weight manifest;
* one ``.bin`` shard — concatenated little-endian float32 weights, in manifest
  order;
* ``export-manifest.json`` — sizes, sha256, provenance, and the parameter count.

The exporter is deliberately **strict**: it understands exactly the layer
sequence of Ma et al. (2023) and refuses anything else, so a silent topology
change cannot slip through as "still 20,193 parameters".

Correctness is not argued, it is measured: ``tools/ai/tfjs_consistency.mjs``
loads the exported model with the real ``@tensorflow/tfjs`` in Node and compares
its five-class probabilities against Keras to <1e-3 on the same clip
(``--reference`` writes that clip and the Keras probabilities).

Usage
-----
    python tools/ai/export_tfjs.py \
        --model data/reports/repro-ma2023/model.keras \
        --out pwa/model \
        --reference data/reports/repro-ma2023/reference.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

CLASSES = ("AS", "MS", "MR", "MVP", "N")
EXPECTED_PARAMS = 20_193
DEFAULT_RAW = Path("data/raw/yaseen/extracted")
# Keras 3 keeps the InputLayer out of ``model.layers`` for a Sequential built
# with ``keras.Input``, so the recorded input shape travels on the Sequential
# config (``build_input_shape``) instead of as a leading layer entry.
EXPECTED_LAYERS = ("Conv1D", "MaxPooling1D", "Conv1D", "MaxPooling1D",
                   "Conv1D", "MaxPooling1D", "Flatten", "Dense", "Dense")


def _initializer(name: str) -> dict:
    return {"class_name": name, "config": {"seed": None}}


def _keras2_layer_config(layer) -> dict:
    """Emit the Keras-2-style config that ``tf.loadLayersModel`` parses.

    Keras 3 ``get_config()`` keeps this architecture's keys compatible, but it
    also carries fields the TF.js loader ignores and defaults we do not want to
    inherit silently, so each layer type is written out explicitly here.
    """
    kind = type(layer).__name__
    name = layer.name
    if kind == "InputLayer":
        shape = list(getattr(layer, "batch_shape", layer.input_shape))
        return {
            "class_name": "InputLayer",
            "config": {
                "batch_input_shape": shape,
                "dtype": "float32",
                "sparse": False,
                "name": name,
            },
        }
    if kind == "Conv1D":
        return {
            "class_name": "Conv1D",
            "config": {
                "name": name,
                "trainable": True,
                "dtype": "float32",
                "filters": int(layer.filters),
                "kernel_size": [int(layer.kernel_size[0])],
                "strides": [int(layer.strides[0])],
                "padding": layer.padding,
                "data_format": "channels_last",
                "dilation_rate": [1],
                "groups": 1,
                "activation": layer.activation.__name__,
                "use_bias": bool(layer.use_bias),
                "kernel_initializer": _initializer("GlorotUniform"),
                "bias_initializer": _initializer("Zeros"),
                "kernel_regularizer": None,
                "bias_regularizer": None,
                "activity_regularizer": None,
                "kernel_constraint": None,
                "bias_constraint": None,
            },
        }
    if kind == "MaxPooling1D":
        return {
            "class_name": "MaxPooling1D",
            "config": {
                "name": name,
                "trainable": True,
                "dtype": "float32",
                "pool_size": [int(layer.pool_size[0])],
                "padding": layer.padding,
                "strides": [int(layer.strides[0])],
                "data_format": "channels_last",
            },
        }
    if kind == "Flatten":
        return {
            "class_name": "Flatten",
            "config": {"name": name, "trainable": True, "dtype": "float32",
                       "data_format": "channels_last"},
        }
    if kind == "Dense":
        return {
            "class_name": "Dense",
            "config": {
                "name": name,
                "trainable": True,
                "dtype": "float32",
                "units": int(layer.units),
                "activation": layer.activation.__name__,
                "use_bias": bool(layer.use_bias),
                "kernel_initializer": _initializer("GlorotUniform"),
                "bias_initializer": _initializer("Zeros"),
                "kernel_regularizer": None,
                "bias_regularizer": None,
                "activity_regularizer": None,
                "kernel_constraint": None,
                "bias_constraint": None,
            },
        }
    raise SystemExit(
        f"layer {name!r} is of type {kind}, which this exporter does not model. "
        "Extend _keras2_layer_config rather than shipping a partly converted model."
    )


def build_topology(model) -> dict:
    kinds = tuple(type(layer).__name__ for layer in model.layers)
    if kinds != EXPECTED_LAYERS:
        raise SystemExit(
            f"unexpected layer sequence {kinds}; expected {EXPECTED_LAYERS}. "
            "This exporter only covers the Ma et al. (2023) architecture."
        )
    input_shape = [None, int(model.input_shape[1]), int(model.input_shape[2])]
    # TF.js rebuilds a Sequential layer by layer and insists that the *first*
    # config in the list carries ``batch_input_shape``; Keras 3 keeps its
    # InputLayer out of ``model.layers``, so it is re-materialised here.  The
    # same shape is repeated on the Sequential config, which is where Keras 2
    # stored it -- either reader finds it.
    input_layer = {
        "class_name": "InputLayer",
        "config": {
            "batch_input_shape": input_shape,
            "dtype": "float32",
            "sparse": False,
            "name": "pcg",
        },
    }
    return {
        "class_name": "Sequential",
        "config": {
            "name": model.name,
            "layers": [input_layer] + [_keras2_layer_config(layer) for layer in model.layers],
            "build_input_shape": input_shape,
        },
        "keras_version": "2.15.0",   # the config dialect written above
        "backend": "tensorflow",
    }


def collect_weights(model) -> list[tuple[str, np.ndarray]]:
    """(name, array) in the order TF.js expects them in the binary shard.

    Names are ``<layer>/<weight>`` exactly as Keras produces them; the TF.js
    loader matches on those names, and keeping the order identical means the
    shard is also readable by a human with the manifest.
    """
    out: list[tuple[str, np.ndarray]] = []
    for layer in model.layers:
        if type(layer).__name__ in ("MaxPooling1D", "Flatten", "InputLayer"):
            continue
        weights = layer.get_weights()
        expected = 1 + int(bool(getattr(layer, "use_bias", False)))
        if weights and len(weights) != expected:
            raise SystemExit(f"{layer.name}: {len(weights)} weight arrays, expected {expected}")
        names = [w.name.split("/")[-1].replace(":0", "") for w in layer.weights]
        for full_name, array in zip(names, weights):
            out.append((f"{layer.name}/{full_name}", np.asarray(array, dtype="<f4")))
    return out


def write_model(model, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    topology = build_topology(model)
    weights = collect_weights(model)

    total_params = int(sum(array.size for _, array in weights))
    if total_params != EXPECTED_PARAMS:
        raise SystemExit(f"exported {total_params} parameters, expected {EXPECTED_PARAMS}")

    shard_name = "group1-shard1of1.bin"
    with (out_dir / shard_name).open("wb") as handle:
        for _, array in weights:
            handle.write(array.tobytes(order="C"))

    manifest = {
        "paths": [shard_name],
        "weights": [
            {"name": name, "shape": list(array.shape), "dtype": "float32"}
            for name, array in weights
        ],
    }
    model_json = {
        "format": "layers-model",
        "generatedBy": "repro_ma2023.py (Keras)",
        "convertedBy": "tools/ai/export_tfjs.py (direct layers-model writer)",
        "modelTopology": topology,
        "weightsManifest": [manifest],
        "userDefinedMetadata": {"class_order": list(CLASSES), "parameters": total_params},
    }
    (out_dir / "model.json").write_text(json.dumps(model_json, indent=1) + "\n", encoding="utf-8")

    shard_bytes = (out_dir / shard_name).stat().st_size
    if shard_bytes != total_params * 4:
        raise SystemExit(f"shard is {shard_bytes} B, expected {total_params * 4} B")
    return {
        "parameters": total_params,
        "shard_bytes": shard_bytes,
        "model_json_bytes": (out_dir / "model.json").stat().st_size,
        "weights": [{"name": n, "shape": list(a.shape)} for n, a in weights],
    }


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_reference(model, out_dir: Path, raw_dir: Path, index: int, seed: int) -> dict:
    """Pick one clip, run Keras on it, and save both sides of the comparison."""
    import repro_ma2023 as rp

    X, y, clips = rp.load_yaseen(raw_dir)
    if not 0 <= index < X.shape[0]:
        raise SystemExit(f"--reference-index {index} outside 0..{X.shape[0] - 1}")
    clip = X[index : index + 1]
    probabilities = model.predict(clip, verbose=0)[0]
    payload = {
        "clip_index": index,
        "recording_id": clips[index].recording_id,
        "label": clips[index].label,
        "class_order": list(CLASSES),
        "input_shape": list(clip.shape),
        # 2,000 float32 values: the exact tensor both sides receive
        "input": [float(v) for v in clip[0, :, 0]],
        "keras_probabilities": [float(v) for v in probabilities],
        "keras_prediction": CLASSES[int(np.argmax(probabilities))],
        "seed": seed,
    }
    path = out_dir / "reference.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    print(f"reference clip {index} ({payload['recording_id']}, {payload['label']}) -> {path}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, required=True, help="trained .keras model")
    parser.add_argument("--out", type=Path, required=True, help="directory for model.json + shard")
    parser.add_argument("--reference", type=Path, default=None,
                        help="write the consistency-check reference JSON here")
    parser.add_argument("--reference-index", type=int, default=0,
                        help="which of the 1,000 clips to use for the check")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    import tensorflow as tf
    from tensorflow import keras

    model = keras.models.load_model(args.model, compile=False)
    model.trainable = False
    info = write_model(model, args.out)
    print(f"wrote {args.out / 'model.json'} ({info['model_json_bytes']} B) and "
          f"{info['shard_bytes']} B of weights")

    manifest = {
        "source_model": str(args.model),
        "source_model_sha256": sha256_of(args.model),
        "tensorflow": tf.__version__,
        "keras": keras.__version__,
        "format": "layers-model (k1)",
        "class_order": list(CLASSES),
        **info,
        "files": {
            "model.json": {"sha256": sha256_of(args.out / "model.json"),
                           "bytes": info["model_json_bytes"]},
            "group1-shard1of1.bin": {"sha256": sha256_of(args.out / "group1-shard1of1.bin"),
                                      "bytes": info["shard_bytes"]},
        },
    }

    if args.reference is not None:
        reference = write_reference(model, args.out, args.raw_dir, args.reference_index, args.seed)
        manifest["reference"] = {
            "clip_index": reference["clip_index"],
            "recording_id": reference["recording_id"],
            "keras_prediction": reference["keras_prediction"],
            "keras_probabilities": reference["keras_probabilities"],
        }

    (args.out / "export-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"manifest -> {args.out / 'export-manifest.json'}")
    return 0



if __name__ == "__main__":
    sys.exit(main())
