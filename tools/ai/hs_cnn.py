"""Small 1D CNN for heart-sound windows plus deployment budget helpers."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import torch
from torch import nn


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


def export_onnx(model: nn.Module, output_path: Path, input_length: int = 8000) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    dummy = torch.zeros(1, input_length)
    torch.onnx.export(
        model,
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
) -> None:
    """Per-channel static int8 quantization using ONNX Runtime."""
    from onnxruntime.quantization import (
        CalibrationDataReader,
        QuantFormat,
        QuantType,
        quantize_static,
    )

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
        per_channel=True,
        weight_type=QuantType.QInt8,
        activation_type=QuantType.QUInt8,
    )
