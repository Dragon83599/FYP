"""Unit tests for the dependency-light heart-sound baseline."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "ai"))

from hs_features import design_bandpass_fir, filtfilt, iter_windows, window_feature_vector
from hs_metrics import auroc, best_threshold, binary_metrics, bootstrap_ci
from hs_models import LogisticRegression, StandardScaler
from hs_segments import (
    annotated_fraction,
    annotated_window_starts,
    annotation_labels,
    parse_segments,
)


class FilterTests(unittest.TestCase):
    def test_bandpass_passes_100hz_and_rejects_5hz(self) -> None:
        sample_rate = 4000
        time = np.arange(sample_rate * 2) / sample_rate
        taps = design_bandpass_fir(sample_rate)
        pass_band = filtfilt(np.sin(2 * np.pi * 100 * time).astype(np.float32), taps)
        stop_band = filtfilt(np.sin(2 * np.pi * 5 * time).astype(np.float32), taps)
        self.assertGreater(np.std(pass_band), 0.5)
        self.assertLess(np.std(stop_band), 0.05)

    def test_window_features_are_finite(self) -> None:
        rng = np.random.default_rng(7)
        signal = rng.normal(0, 0.1, 8000).astype(np.float32)
        features = window_feature_vector(signal, 4000)
        self.assertEqual(features.shape, (40 * 3 + 8,))
        self.assertTrue(np.all(np.isfinite(features)))

    def test_iter_windows_bounds(self) -> None:
        windows = list(iter_windows(np.arange(10), 4, 2))
        self.assertEqual([start for start, _ in windows], [0, 2, 4, 6])


class ModelTests(unittest.TestCase):
    def test_logistic_regression_separates_shifted_classes(self) -> None:
        rng = np.random.default_rng(11)
        negatives = rng.normal(-1.0, 0.5, size=(80, 4))
        positives = rng.normal(1.0, 0.5, size=(80, 4))
        x = np.vstack([negatives, positives])
        y = np.concatenate([np.zeros(80, dtype=int), np.ones(80, dtype=int)])
        scaler = StandardScaler().fit(x)
        model = LogisticRegression(epochs=300, learning_rate=0.05).fit(
            scaler.transform(x), y
        )
        probability = model.predict_proba(scaler.transform(x))
        metrics = binary_metrics(y, probability)
        self.assertGreater(metrics["balanced_accuracy"], 0.95)

    def test_auroc_for_perfect_ranking(self) -> None:
        self.assertAlmostEqual(auroc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])), 1.0)

    def test_bootstrap_ci_for_separated_scores(self) -> None:
        y = np.array([0, 0, 0, 0, 1, 1, 1, 1])
        probability = np.array([0.1, 0.2, 0.15, 0.25, 0.8, 0.9, 0.85, 0.75])
        interval = bootstrap_ci(y, probability, "auroc", n_boot=300, seed=1)
        self.assertGreater(interval["low"], 0.8)
        self.assertLessEqual(interval["high"], 1.0)

    def test_best_threshold_breaks_ties_towards_midpoint(self) -> None:
        y = np.array([0, 1, 0, 1])
        probability = np.array([0.1, 0.2, 0.3, 0.4])
        threshold, score = best_threshold(y, probability)
        self.assertAlmostEqual(score, 0.75)
        self.assertAlmostEqual(threshold, 0.4)


class SegmentTests(unittest.TestCase):
    def test_segments_mask_and_window_selection(self) -> None:
        segments = parse_segments("0\t1\t0\n1\t2\t1\n2\t3\t4\n")
        self.assertEqual(segments.shape, (3, 3))
        labels = annotation_labels(4000, segments, 1000)
        self.assertEqual(int(labels[500]), 0)
        self.assertEqual(int(labels[1500]), 1)
        self.assertEqual(int(labels[2500]), 4)
        self.assertAlmostEqual(annotated_fraction(labels, 0, 4000), 0.5)
        starts = annotated_window_starts(4000, segments, 1000, 1000, 1000, 0.99)
        self.assertEqual(starts.tolist(), [1000, 2000])


if __name__ == "__main__":
    unittest.main()
