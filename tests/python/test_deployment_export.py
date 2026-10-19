"""Deployment-form export: input standardization moved out of the graph.

The device will run the model body only (Conv/Relu/MaxPool/ReduceMean/Gemm);
the per-window standardization is done in C. These tests pin the contract:

- ``normalize_window`` must match the training-time definition exactly.
- the body-only export must contain no normalization ops and must reproduce the
  full model's logits when fed normalized input.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "ai"))

from hs_cnn import normalize_window  # noqa: E402


def has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - CI installs NumPy only
        return False


def has_onnxruntime() -> bool:
    try:
        import onnxruntime  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


class NormalizeWindowTests(unittest.TestCase):
    def test_matches_manual_definition(self) -> None:
        rng = np.random.default_rng(0)
        windows = rng.normal(loc=3.0, scale=2.0, size=(4, 8000)).astype(np.float32)
        out = normalize_window(windows)
        expected = (windows - windows.mean(axis=1, keepdims=True)) / (
            windows.std(axis=1, keepdims=True) + 1e-6
        )
        np.testing.assert_allclose(out, expected, rtol=0, atol=1e-6)

    def test_each_window_is_zero_mean_unit_std(self) -> None:
        rng = np.random.default_rng(1)
        out = normalize_window(rng.normal(size=(5, 1000)).astype(np.float32))
        self.assertTrue(np.allclose(out.mean(axis=1), 0.0, atol=1e-5))
        self.assertTrue(np.allclose(out.std(axis=1), 1.0, atol=1e-4))

    def test_preserves_3d_shape(self) -> None:
        x = np.zeros((2, 1, 64), dtype=np.float32)
        self.assertEqual(normalize_window(x).shape, (2, 1, 64))

    def test_constant_window_does_not_explode(self) -> None:
        out = normalize_window(np.full((1, 32), 7.0, dtype=np.float32))
        self.assertTrue(np.all(np.isfinite(out)))


@unittest.skipUnless(has_torch() and has_onnxruntime(), "needs torch and onnxruntime")
class BodyOnlyExportTests(unittest.TestCase):
    def test_body_export_has_no_normalization_ops_and_matches(self) -> None:
        import collections

        import onnx
        import onnxruntime as ort
        import torch

        from hs_cnn import Small1DCNN, export_onnx

        torch.manual_seed(0)
        model = Small1DCNN()
        model.eval()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "body.onnx"
            export_onnx(model, path, include_normalization=False)
            graph = onnx.load(str(path)).graph
            ops = collections.Counter(node.op_type for node in graph.node)
            for forbidden in ("Div", "Sqrt", "Sub"):
                self.assertNotIn(forbidden, ops, f"{forbidden} belongs to the normalization")
            self.assertEqual(ops.get("Conv"), 4)
            self.assertEqual(ops.get("Gemm"), 1)

            rng = np.random.default_rng(2)
            x = rng.normal(size=(3, 8000)).astype(np.float32)
            session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            body = session.run(None, {"waveform": normalize_window(x)})[0].reshape(-1)
            with torch.no_grad():
                full = model(torch.from_numpy(x)).numpy()
            np.testing.assert_allclose(body, full, rtol=0, atol=5e-3)


if __name__ == "__main__":
    unittest.main()
