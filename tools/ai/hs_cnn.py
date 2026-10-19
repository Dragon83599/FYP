"""1D CNNs for heart-sound windows plus deployment budget helpers.

``Small1DCNN`` is the frozen baseline: 41,105 parameters, 5.72 M MAC per 2 s
window, small enough for an ESP32-S3 class host.  Its definition and defaults
are unchanged.

The capacity experiment (``tools/ai/large_model_experiment.py``) needs larger
models that differ *only* in capacity, so two larger architectures are built
here from the pure-Python table in ``large_model_experiment``:

* :class:`ConvNet1D` - a generic stem + residual/dilated trunk + global-average-
  pool classifier, instantiated as the ``resnet`` and ``tcn`` specs;
* :func:`build_model` - the one constructor both the experiment and
  ``cross_validate.py --model {cnn,small,resnet,tcn}`` use, so the architecture
  table, the torch modules and the reported parameter/MAC counts cannot drift
  apart.

The table lives in ``large_model_experiment.py`` rather than here because CI
installs NumPy only (``.github/workflows/ci.yml``) and that module has to stay
importable without torch; the modules below are generated from it and are
pinned to it by ``tests/python/test_large_model.py``.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

from large_model_experiment import (
    ARCHITECTURE_SPECS,
    CONV_ARCHITECTURES,
    SMALL_ALIASES,
    ConvNetSpec,
    architecture_macs,
    architecture_parameters,
)


class ConvBlock(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, kernel: int, stride: int, pool: int) -> None:
        super().__init__()
        self.conv = nn.Conv1d(
            in_channels,
            out_channels,
            kernel_size=kernel,
            stride=stride,
            padding=kernel // 2,
            bias=False,
        )
        self.norm = nn.BatchNorm1d(out_channels)
        self.activation = nn.ReLU()
        self.pool = nn.MaxPool1d(pool)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.pool(self.activation(self.norm(self.conv(x))))


class Small1DCNN(nn.Module):
    """Roughly 41k parameters; designed to fit an ESP32-S3 class device."""

    def __init__(self, dropout: float = 0.2) -> None:
        super().__init__()
        self.features = nn.Sequential(
            ConvBlock(1, 16, kernel=15, stride=4, pool=2),
            ConvBlock(16, 32, kernel=11, stride=2, pool=2),
            ConvBlock(32, 64, kernel=7, stride=2, pool=2),
            ConvBlock(64, 64, kernel=5, stride=2, pool=2),
        )
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(64, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 2:
            x = x.unsqueeze(1)
        mean = x.mean(dim=2, keepdim=True)
        std = x.std(dim=2, keepdim=True)
        x = (x - mean) / (std + 1e-6)
        x = self.features(x)
        x = self.pool(x).squeeze(-1)
        return self.classifier(self.dropout(x)).squeeze(-1)


class ResidualBlock1D(nn.Module):
    """Post-activation residual block with an optional 1x1 projection shortcut.

    ``dilation`` is only used by the dilated TCN arm; with ``dilation == 1``,
    ``stride == 1`` and ``in_channels == out_channels`` this is the plain
    two-convolution residual block of a 1D ResNet.  The shortcut is a 1x1
    convolution + BatchNorm whenever the stride or the channel count changes,
    which is also where all of the block's extra parameters sit.
    """

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel: int,
        stride: int = 1,
        dilation: int = 1,
    ) -> None:
        super().__init__()
        padding = dilation * (kernel // 2)
        self.conv1 = nn.Conv1d(
            in_channels,
            out_channels,
            kernel_size=kernel,
            stride=stride,
            padding=padding,
            dilation=dilation,
            bias=False,
        )
        self.norm1 = nn.BatchNorm1d(out_channels)
        self.conv2 = nn.Conv1d(
            out_channels,
            out_channels,
            kernel_size=kernel,
            stride=1,
            padding=padding,
            dilation=dilation,
            bias=False,
        )
        self.norm2 = nn.BatchNorm1d(out_channels)
        if stride != 1 or in_channels != out_channels:
            self.shortcut: nn.Module = nn.Sequential(
                nn.Conv1d(in_channels, out_channels, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm1d(out_channels),
            )
        else:
            self.shortcut = nn.Identity()
        self.activation = nn.ReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.activation(self.norm1(self.conv1(x)))
        out = self.norm2(self.conv2(out))
        return self.activation(out + self.shortcut(x))


class ConvNet1D(nn.Module):
    """A convolutional net built from a :class:`ConvNetSpec` (capacity arms).

    Deliberately shares everything with ``Small1DCNN`` except capacity: per
    window standardisation of the raw waveform, ``bias=False`` convolutions with
    BatchNorm, a max-pooled stem, global average pooling and a single linear
    logit.  ``ConvNet1D(ARCHITECTURE_SPECS["small"])`` therefore *is* the small
    CNN's architecture (the tests pin its parameter count to ``Small1DCNN``).
    """

    def __init__(self, spec: ConvNetSpec) -> None:
        super().__init__()
        self.spec = spec
        self.stem = ConvBlock(1, spec.stem_channels, kernel=spec.stem_kernel, stride=spec.stem_stride, pool=spec.stem_pool)
        stages: list[nn.Module] = []
        for stage in spec.stages:
            dilations = stage.dilations or (1,) * stage.blocks
            if len(dilations) != stage.blocks:
                raise ValueError(
                    f"{spec.name}: {len(dilations)} dilation(s) for {stage.blocks} block(s)"
                )
            blocks: list[nn.Module] = []
            for index in range(stage.blocks):
                in_channels = stage.in_channels if index == 0 else stage.channels
                block_stride = stage.stride if index == 0 else 1
                if stage.residual:
                    blocks.append(
                        ResidualBlock1D(
                            in_channels,
                            stage.channels,
                            kernel=stage.kernel,
                            stride=block_stride,
                            dilation=dilations[index],
                        )
                    )
                else:
                    blocks.append(
                        ConvBlock(
                            in_channels,
                            stage.channels,
                            kernel=stage.kernel,
                            stride=block_stride,
                            pool=1,
                        )
                    )
            if stage.pool > 1:
                blocks.append(nn.MaxPool1d(stage.pool))
            stages.append(nn.Sequential(*blocks))
        self.features = nn.Sequential(*stages)
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.dropout = nn.Dropout(spec.head_dropout)
        self.classifier = nn.Linear(spec.stages[-1].channels, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 2:
            x = x.unsqueeze(1)
        mean = x.mean(dim=2, keepdim=True)
        std = x.std(dim=2, keepdim=True)
        x = (x - mean) / (std + 1e-6)
        x = self.stem(x)
        x = self.features(x)
        x = self.pool(x).squeeze(-1)
        return self.classifier(self.dropout(x)).squeeze(-1)


def build_model(name: str, dropout: float | None = None) -> nn.Module:
    """Return the torch module for an architecture name.

    ``small``/``cnn`` return ``Small1DCNN`` itself (the frozen baseline, so the
    control arm is byte-for-byte the published model); the other names return a
    :class:`ConvNet1D` built from ``large_model_experiment.ARCHITECTURE_SPECS``.
    """
    key = str(name).lower()
    if key in SMALL_ALIASES:
        return Small1DCNN() if dropout is None else Small1DCNN(dropout=dropout)
    if key not in ARCHITECTURE_SPECS:
        raise ValueError(
            f"unknown architecture {name!r}; expected one of "
            f"{sorted(set(CONV_ARCHITECTURES) | set(SMALL_ALIASES))}"
        )
    spec = ARCHITECTURE_SPECS[key]
    if dropout is not None:
        spec = dataclasses.replace(spec, head_dropout=float(dropout))
    return ConvNet1D(spec)


def model_budget(model: nn.Module, input_length: int = 8000) -> dict:
    """Measured parameters and MACs of an instantiated model."""
    return {
        "parameters": count_parameters(model),
        "macs_per_window": measure_macs(model, input_length),
    }


def count_parameters(model: nn.Module) -> int:
    return int(sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad))


def measure_macs(model: nn.Module, input_length: int = 8000) -> int:
    """Count multiply-accumulate operations for Conv1d and Linear layers."""
    total = 0
    handles = []

    def conv_hook(module: nn.Conv1d, inputs, output) -> None:
        nonlocal total
        out_length = output.shape[-1]
        total += int(out_length * module.out_channels * module.in_channels * module.kernel_size[0])

    def linear_hook(module: nn.Linear, inputs, output) -> None:
        nonlocal total
        total += int(module.in_features * module.out_features)

    for module in model.modules():
        if isinstance(module, nn.Conv1d):
            handles.append(module.register_forward_hook(conv_hook))
        elif isinstance(module, nn.Linear):
            handles.append(module.register_forward_hook(linear_hook))
    was_training = model.training
    model.eval()
    with torch.no_grad():
        model(torch.zeros(1, input_length))
    for handle in handles:
        handle.remove()
    if was_training:
        model.train()
    return total


def measure_latency(model: nn.Module, input_length: int = 8000, runs: int = 200, warmup: int = 20) -> dict:
    model.eval()
    sample = torch.zeros(1, input_length)
    with torch.no_grad():
        for _ in range(warmup):
            model(sample)
        timings = []
        for _ in range(runs):
            started = time.perf_counter()
            model(sample)
            timings.append((time.perf_counter() - started) * 1000.0)
    values = np.asarray(timings)
    return {
        "runs": runs,
        "mean_ms": float(values.mean()),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
        "max_ms": float(values.max()),
    }


class NormalizationFreeCNN(nn.Module):
    """``Small1DCNN`` body with the input standardization removed.

    ``Small1DCNN.forward`` standardizes each window (subtract mean, divide by
    std) before the convolutions. That is three ``ReduceMean`` nodes, a ``Sub``,
    a ``Div`` and a ``Sqrt`` in the exported graph - arithmetic an MCU integer
    kernel set does not necessarily provide, and a per-window pass over 8000
    samples that the host can do in integer code far more cheaply.

    Wrapping the trained model this way keeps the weights identical and moves
    the standardization to the caller. ``normalize_window`` below is the
    reference implementation the device must reproduce.
    """

    def __init__(self, model: "Small1DCNN") -> None:
        super().__init__()
        self.model = model

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 2:
            x = x.unsqueeze(1)
        features = self.model.features(x)
        pooled = self.model.pool(features).squeeze(-1)
        return self.model.classifier(pooled).squeeze(-1)


def normalize_window(windows: np.ndarray, epsilon: float = 1e-6) -> np.ndarray:
    """Reference input standardization, shared by the exporter and the tests.

    ``windows`` is ``[n, samples]`` or ``[n, 1, samples]``; returns the same
    shape as float32. The device must reproduce this with ``mean`` and
    ``std`` over the window (population std, as ``numpy.std`` / ``torch.std``
    use by default here).
    """
    array = np.asarray(windows, dtype=np.float32)
    mean = array.mean(axis=-1, keepdims=True)
    std = array.std(axis=-1, keepdims=True)
    return (array - mean) / (std + epsilon)


def export_onnx(
    model: nn.Module,
    output_path: Path,
    input_length: int = 8000,
    *,
    include_normalization: bool = True,
) -> None:
    """Export to ONNX.

    ``include_normalization=False`` exports the body only (see
    :class:`NormalizationFreeCNN`), which is the deployment form: the caller
    standardizes the window with :func:`normalize_window` semantics before
    feeding it. The default keeps the historical behaviour so existing reports
    stay reproducible.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    exported = model if include_normalization else NormalizationFreeCNN(model)
    exported.eval()
    dummy = torch.zeros(1, input_length)
    torch.onnx.export(
        exported,
        dummy,
        str(output_path),
        input_names=["waveform"],
        output_names=["logit"],
        dynamic_axes={"waveform": {0: "batch"}, "logit": {0: "batch"}},
        opset_version=18,
    )


def quantize_onnx_static(
    input_path: Path,
    output_path: Path,
    calibration_windows: np.ndarray,
    *,
    activation_type: str = "int8",
    per_channel: bool = True,
    calibrate_method: str = "percentile",
    percentile: float = 99.99,
) -> None:
    """Per-channel static int8 quantization using ONNX Runtime.

    The keyword defaults are the corrected recipe (2026-10-19). They are also
    what an MCU integer kernel can run, which is why they are the default rather
    than ONNX Runtime's:

    * ``activation_type="int8"`` - symmetric ``QInt8`` activations. ORT's
      ``QUInt8`` default is not offered by ESP-DL / TFLite Micro integer kernels,
      and it costs accuracy here: over the five frozen folds the mean patient
      AUROC drop was 0.0937 with ``QUInt8`` versus 0.0532 with ``QInt8`` on the
      same MinMax/linspace/200 configuration (``data/reports/int8-calibration-sweep``).
    * ``calibrate_method="percentile"`` with ``percentile=99.99`` - MinMax lets
      one outlier window stretch the activation range. Note ORT's own
      ``CalibPercentile`` default is 99.999, which for a few hundred windows is
      numerically identical to MinMax, so the percentile has to be passed
      explicitly.
    * ``per_channel=True`` - per-channel weight scales, unchanged.

    The old three-argument call still works and simply uses these defaults; pass
    ``activation_type="quint8"`` / ``calibrate_method="minmax"`` to reproduce the
    pre-2026-10-19 numbers (``QUInt8`` + MinMax + ``np.linspace`` calibration).

    ONNX Runtime is imported lazily so this module's import surface (and the
    NumPy-only CI) is unaffected.
    """
    from onnxruntime.quantization import (
        CalibrationDataReader,
        CalibrationMethod,
        QuantFormat,
        QuantType,
        quantize_static,
    )

    activation = {
        "int8": QuantType.QInt8,
        "quint8": QuantType.QUInt8,
    }[activation_type]
    method = {
        "percentile": CalibrationMethod.Percentile,
        "minmax": CalibrationMethod.MinMax,
        "entropy": CalibrationMethod.Entropy,
    }[calibrate_method]
    extra_options = None
    if method is CalibrationMethod.Percentile:
        extra_options = {"CalibPercentile": float(percentile)}

    class Reader(CalibrationDataReader):
        def __init__(self, windows: np.ndarray) -> None:
            self._iterator = iter(windows)

        def get_next(self):
            try:
                window = next(self._iterator).astype(np.float32)
            except StopIteration:
                return None
            return {"waveform": window[None, :]}

        def rewind(self) -> None:
            pass

    quantize_static(
        model_input=str(input_path),
        model_output=str(output_path),
        calibration_data_reader=Reader(calibration_windows),
        quant_format=QuantFormat.QOperator,
        per_channel=per_channel,
        weight_type=QuantType.QInt8,
        activation_type=activation,
        calibrate_method=method,
        extra_options=extra_options,
    )
