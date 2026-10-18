"""Unit tests for the INT8 quantization recipe's testable pieces.

The quantizer itself needs torch (the model) and ONNX Runtime (the exporter), so
it is exercised end-to-end by ``tools/ai/train_cnn.py`` on the frozen folds, not
here. What *can* be pinned cheaply is the part that caused the 2026-10-19
regression to be missed in the first place:

1. **Model size accounting** - ``model-fp32.onnx`` keeps its weights in a
   ``.data`` sidecar, so counting the bare ``.onnx`` understated the FP32 model by
   162,752 B and made INT8 look *larger* than FP32. ``requantize.model_size_bytes``
   must count graph + sidecar.
2. **Calibration sampling** - the calibration set has to be reproducible from a
   seed (so a report can be re-derived) and must not depend on the fold only by
   accident. The fold is part of the seed.
3. **Threshold selection** - the INT8 decision threshold must be derived from
   INT8 validation probabilities, because quantization shifts the score
   distribution. A synthetic shifted-score case shows why a fixed 0.5 (or an
   FP32-derived) threshold reports a balanced accuracy collapse while the
   ranking is intact.

These tests are NumPy-only: they import ``requantize`` (torch-free,
onnxruntime-free) and ``hs_metrics``, so they run in the NumPy-only CI.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "ai"))

from hs_metrics import best_threshold, binary_metrics
from requantize import calibration_index, model_size_bytes, published_config


class ModelSizeAccountingTest(unittest.TestCase):
    """A model is the .onnx graph *plus* its external-data sidecar."""

    def _write(self, path: Path, n_bytes: int) -> None:
        path.write_bytes(b"\0" * n_bytes)

    def test_external_data_sidecar_is_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            onnx_path = Path(tmp) / "model-fp32.onnx"
            self._write(onnx_path, 100)
            self._write(Path(tmp) / "model-fp32.onnx.data", 250)
            size = model_size_bytes(onnx_path)
            self.assertEqual(size["onnx_bytes"], 100)
            self.assertEqual(size["external_data_bytes"], 250)
            self.assertEqual(size["total_bytes"], 350)
            self.assertEqual(size["external_data_files"], ["model-fp32.onnx.data"])

    def test_self_contained_model_counts_only_the_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            onnx_path = Path(tmp) / "model-int8.onnx"
            self._write(onnx_path, 4242)
            size = model_size_bytes(onnx_path)
            self.assertEqual(size["onnx_bytes"], 4242)
            self.assertEqual(size["external_data_bytes"], 0)
            self.assertEqual(size["total_bytes"], 4242)
            self.assertEqual(size["external_data_files"], [])

    def test_export_shaped_pair_matches_the_published_numbers(self):
        """The real fold-0 export is 37,717 B + 162,752 B = 200,469 B."""
        with tempfile.TemporaryDirectory() as tmp:
            onnx_path = Path(tmp) / "model-fp32.onnx"
            self._write(onnx_path, 37717)
            self._write(Path(tmp) / "model-fp32.onnx.data", 162752)
            self.assertEqual(model_size_bytes(onnx_path)["total_bytes"], 200469)


class CalibrationSamplingTest(unittest.TestCase):
    """Seeded uniform subsampling: deterministic, fold-aware, well covered."""

    def test_same_seed_and_fold_reproduce_the_same_windows(self):
        first = calibration_index(200, "random", 8000, 20261018, 0, fold=3)
        second = calibration_index(200, "random", 8000, 20261018, 0, fold=3)
        self.assertTrue((first == second).all())

    def test_folds_do_not_share_one_draw(self):
        fold3 = calibration_index(200, "random", 8000, 20261018, 0, fold=3)
        fold4 = calibration_index(200, "random", 8000, 20261018, 0, fold=4)
        self.assertFalse((fold3 == fold4).all())

    def test_repeats_differ_so_a_spread_can_be_estimated(self):
        first = calibration_index(200, "random", 8000, 20261018, 0, fold=0)
        second = calibration_index(200, "random", 8000, 20261018, 1, fold=0)
        self.assertFalse((first == second).all())

    def test_indices_are_unique_sorted_and_in_range(self):
        index = calibration_index(200, "random", 8000, 20261018, 0, fold=0)
        self.assertEqual(len(index), 200)
        self.assertEqual(len(set(int(value) for value in index)), 200)
        self.assertTrue((index[1:] > index[:-1]).all())
        self.assertGreaterEqual(int(index.min()), 0)
        self.assertLess(int(index.max()), 8000)

    def test_subsample_covers_many_recordings_not_a_few(self):
        """The old linspace-vs-random hypothesis: both must span the cache.

        With one window picked per ~40 cached windows, a 200/8000 subsample
        touches 200 distinct positions, i.e. it is not concentrated in a handful
        of recordings. Pinned because "the calibration set covers only a few
        recordings" was the original (wrong) diagnosis.
        """
        index = calibration_index(200, "random", 8000, 20261018, 0, fold=0)
        # Window i belongs to recording i // 4 (max 4 windows per recording).
        recordings = set(int(value) // 4 for value in index)
        self.assertGreater(len(recordings), 150)

    def test_linspace_mode_is_still_available_for_old_reports(self):
        index = calibration_index(200, "linspace", 8000, 20261018, 0, fold=0)
        self.assertEqual(len(index), 200)
        self.assertEqual(int(index[0]), 0)
        self.assertEqual(int(index[-1]), 7999)

    def test_size_larger_than_the_cache_is_clamped_not_crashing(self):
        index = calibration_index(500, "random", 120, 1, 0, fold=0)
        self.assertEqual(len(index), 120)
        self.assertEqual(len(set(int(value) for value in index)), 120)


class ThresholdSelectionTest(unittest.TestCase):
    """The INT8 threshold must come from INT8 validation probabilities."""

    def test_separable_scores_get_a_separating_threshold(self):
        labels = [0, 0, 1, 1]
        scores = [0.05, 0.20, 0.80, 0.90]
        threshold, score = best_threshold(labels, scores)
        self.assertAlmostEqual(score, 1.0)
        self.assertGreater(threshold, 0.20)
        self.assertLessEqual(threshold, 0.80)

    def test_degenerate_all_tied_scores_still_return_a_threshold(self):
        """Fully tied scores cannot separate the classes; the result must be usable.

        Every candidate threshold scores balanced accuracy 0.5, so this pins the
        tie-break's determinism (it keeps the first candidate, not an exception
        or an out-of-range value).
        """
        labels = [0, 1, 0, 1]
        scores = [0.0, 0.0, 1.0, 1.0]
        threshold, score = best_threshold(labels, scores)
        self.assertAlmostEqual(score, 0.5)
        self.assertGreaterEqual(threshold, 0.0)
        self.assertLessEqual(threshold, 1.0)

    def test_a_shifted_score_distribution_needs_its_own_threshold(self):
        """Why change 4 exists: INT8 compresses scores towards one end.

        The same ranking scores perfectly at its own threshold, while the
        FP32-derived 0.5 threshold labels almost everything negative. AUROC is
        identical in both cases; balanced accuracy is not.
        """
        labels = [0, 0, 0, 1, 1, 1]
        int8_scores = [0.02, 0.04, 0.06, 0.08, 0.10, 0.12]  # shifted down, ranking intact
        at_fixed = binary_metrics(labels, int8_scores, 0.5)
        own_threshold, own_score = best_threshold(labels, int8_scores)
        self.assertLess(own_threshold, 0.5)
        self.assertAlmostEqual(own_score, 1.0)
        self.assertAlmostEqual(at_fixed["balanced_accuracy"], 0.5)
        self.assertAlmostEqual(at_fixed["auroc"], 1.0)

    def test_empty_positive_class_does_not_crash(self):
        threshold, score = best_threshold([0, 0, 0], [0.1, 0.2, 0.3])
        self.assertIsInstance(threshold, float)
        self.assertIsInstance(score, float)


class PublishedConfigTest(unittest.TestCase):
    """The report's recorded recipe maps back to a sweep config name."""

    def test_old_reports_fall_back_to_the_pre_2026_recipe(self):
        self.assertEqual(published_config(None),
                         "qoperator/quint8/minmax/n200/linspace")

    def test_new_recipe_includes_the_percentile(self):
        settings = {
            "format": "QOperator",
            "activation_type": "int8",
            "calibrate_method": "percentile",
            "calibrate_percentile": 99.99,
            "calibration_size": 200,
            "calibration_sampling": "random",
        }
        self.assertEqual(published_config(settings),
                         "qoperator/int8/percentile/n200/random@99.99")

    def test_minmax_has_no_percentile_suffix(self):
        settings = {
            "format": "QOperator",
            "activation_type": "int8",
            "calibrate_method": "minmax",
            "calibration_size": 1000,
            "calibration_sampling": "linspace",
        }
        self.assertEqual(published_config(settings),
                         "qoperator/int8/minmax/n1000/linspace")


if __name__ == "__main__":
    unittest.main()
