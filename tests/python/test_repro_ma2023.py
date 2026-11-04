"""Tests for the Ma et al. (2023) reproduction script.

These tests run under the NumPy-only CI job, so they cover everything that does
not need TensorFlow: the parameter arithmetic that pins ``padding='same'``, the
pre-processing rules copied from the paper, and the fold assignment.  The
TensorFlow build is checked by ``repro_ma2023.py`` itself, which refuses to
train unless ``count_params()`` equals 20,193.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "ai"))

from repro_ma2023 import (  # noqa: E402
    CLASSES,
    EXPECTED_PARAMS,
    N_SAMPLES,
    crop_or_pad,
    inner_val_mask,
    macro_f1,
    min_max_scale,
    parameter_breakdown,
    stratified_folds,
    t_ci95,
)


class ParameterArithmeticTest(unittest.TestCase):
    def test_paper_count_is_reproduced_with_same_padding(self):
        breakdown = parameter_breakdown("same")
        self.assertEqual(breakdown["total"], EXPECTED_PARAMS)
        self.assertEqual(breakdown["total"], 20_193)

    def test_valid_padding_would_not_match_the_paper(self):
        # 17,121 != 20,193 is the evidence for 'same', not a preference.
        self.assertEqual(parameter_breakdown("valid")["total"], 17_121)

    def test_layer_breakdown_matches_the_published_layer_list(self):
        breakdown = parameter_breakdown("same")
        # 16 filters x (1 channel x kernel 20 + bias)
        self.assertEqual(breakdown["conv1"], 336)
        self.assertEqual(breakdown["conv2"], 2_568)
        self.assertEqual(breakdown["conv3"], 644)
        # 2000 -> 500 -> 125 -> 31 (valid pooling), 31 x 4 features
        self.assertEqual(breakdown["flatten_features"], 124)
        self.assertEqual(breakdown["fc"], 16_000)
        self.assertEqual(breakdown["out"], 645)
        self.assertEqual(
            breakdown["total"],
            breakdown["conv1"] + breakdown["conv2"] + breakdown["conv3"]
            + breakdown["fc"] + breakdown["out"],
        )

    def test_unknown_padding_is_rejected(self):
        with self.assertRaises(ValueError):
            parameter_breakdown("causal")


class PreprocessingTest(unittest.TestCase):
    def test_long_recording_is_cropped_from_the_start(self):
        x = np.arange(3 * N_SAMPLES, dtype=np.float64)
        y, cropped, padded = crop_or_pad(x)
        self.assertTrue(cropped)
        self.assertFalse(padded)
        self.assertEqual(y.shape[0], N_SAMPLES)
        self.assertEqual(y[0], 0.0)            # keeps the S1-aligned beginning

    def test_short_recording_is_zero_padded_at_the_tail(self):
        y, cropped, padded = crop_or_pad(np.ones(1_500))
        self.assertFalse(cropped)
        self.assertTrue(padded)
        self.assertEqual(y.shape[0], N_SAMPLES)
        self.assertEqual(y[:1_500].sum(), 1_500.0)
        self.assertEqual(y[1_500:].sum(), 0.0)

    def test_exact_length_is_left_alone(self):
        y, cropped, padded = crop_or_pad(np.zeros(N_SAMPLES))
        self.assertEqual((cropped, padded), (False, False))

    def test_min_max_lands_on_minus_one_to_one(self):
        y = min_max_scale(np.linspace(-3.0, 7.0, 101))
        self.assertAlmostEqual(float(y.min()), -1.0)
        self.assertAlmostEqual(float(y.max()), 1.0)

    def test_constant_clip_becomes_zeros_instead_of_nan(self):
        y = min_max_scale(np.full(10, 0.25))
        self.assertTrue(np.all(y == 0.0))


class FoldTest(unittest.TestCase):
    def setUp(self):
        # The real dataset is exactly 200 clips in each of the five classes.
        self.y = np.repeat(np.arange(len(CLASSES)), 200)

    def test_every_fold_gets_one_tenth_of_each_class(self):
        folds = stratified_folds(self.y, 10, 42)
        self.assertEqual(folds.shape, self.y.shape)
        self.assertEqual(sorted(set(folds.tolist())), list(range(10)))
        for label in range(len(CLASSES)):
            counts = np.bincount(folds[self.y == label], minlength=10)
            self.assertTrue(np.all(counts == 20))

    def test_each_clip_lands_in_exactly_one_fold(self):
        folds = stratified_folds(self.y, 10, 42)
        self.assertEqual(int(np.bincount(folds).sum()), self.y.size)
        self.assertEqual(int(np.bincount(folds).max()), 100)

    def test_seed_changes_the_assignment_but_not_the_balance(self):
        a = stratified_folds(self.y, 10, 42)
        b = stratified_folds(self.y, 10, 123)
        self.assertFalse(np.array_equal(a, b))
        self.assertEqual(sorted(np.bincount(b).tolist()), [100] * 10)

    def test_inner_validation_slice_is_stratified_and_small(self):
        mask = inner_val_mask(self.y, seed=42, fraction=0.10)
        train_size = self.y.size - int(mask.sum())
        self.assertAlmostEqual(train_size / self.y.size, 0.90, places=2)
        for label in range(len(CLASSES)):
            share = mask[self.y == label].mean()
            self.assertAlmostEqual(float(share), 0.10, places=2)


class MetricTest(unittest.TestCase):
    def test_macro_f1_of_a_perfect_matrix_is_one(self):
        self.assertAlmostEqual(macro_f1(np.eye(5, dtype=int) * 200), 1.0)

    def test_macro_f1_is_the_unweighted_mean_of_class_f1(self):
        # Two classes, each 2 samples; one mistake in each direction.
        cm = np.array([[1, 1], [1, 1]])
        self.assertAlmostEqual(macro_f1(cm), 0.5)

    def test_t_interval_brackets_the_mean(self):
        try:
            import scipy  # noqa: F401
        except ImportError:  # NumPy-only CI job
            self.skipTest("scipy not installed")
        values = np.array([98.0, 98.5, 99.0, 98.2])
        low, high = t_ci95(values)
        self.assertLess(low, values.mean())
        self.assertGreater(high, values.mean())
        self.assertAlmostEqual((low + high) / 2, float(values.mean()), places=9)

    def test_single_value_has_no_interval(self):
        low, high = t_ci95(np.array([98.0]))
        self.assertTrue(np.isnan(low) and np.isnan(high))


if __name__ == "__main__":
    unittest.main()
