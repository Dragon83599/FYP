"""Unit tests for the MFCC + delta feature front-end and its CV pipeline."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "ai"))

from hs_mfcc import (
    AGGREGATIONS,
    FEATURE_SETS,
    dct_matrix,
    delta_delta,
    deltas,
    mfcc,
    mfcc_feature_dim,
    mfcc_window_vector,
)

SAMPLE_RATE = 4000
WINDOW_SAMPLES = 8000  # 2 s at 4 kHz, same as the training protocol
EXPECTED_FRAMES = 1 + (WINDOW_SAMPLES - 256) // 100


def make_signal(kind: str, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind == "noise":
        return rng.normal(0.0, 0.1, WINDOW_SAMPLES).astype(np.float32)
    if kind == "silence":
        return np.zeros(WINDOW_SAMPLES, dtype=np.float32)
    if kind == "tone":
        time = np.arange(WINDOW_SAMPLES) / SAMPLE_RATE
        return (0.5 * np.sin(2.0 * np.pi * 440.0 * time)).astype(np.float32)
    raise ValueError(kind)


class DctTests(unittest.TestCase):
    def test_dct_matrix_matches_direct_computation(self) -> None:
        """The matrix form must equal the textbook DCT-II formula, term by term."""
        rng = np.random.default_rng(3)
        x = rng.normal(size=9)
        n = len(x)
        positions = 2 * np.arange(n) + 1
        expected = np.asarray(
            [
                np.sqrt(2.0 / n)
                * (1.0 / np.sqrt(2.0) if k == 0 else 1.0)
                * np.sum(x * np.cos(np.pi * k * positions / (2.0 * n)))
                for k in range(n)
            ]
        )
        np.testing.assert_allclose(dct_matrix(n) @ x, expected, atol=1e-12)

    def test_unnormalized_dct_matches_direct_computation(self) -> None:
        rng = np.random.default_rng(4)
        x = rng.normal(size=6)
        n = len(x)
        positions = 2 * np.arange(n) + 1
        expected = np.asarray(
            [
                (1.0 if k == 0 else 2.0)
                * np.sum(x * np.cos(np.pi * k * positions / (2.0 * n)))
                for k in range(n)
            ]
        )
        np.testing.assert_allclose(dct_matrix(n, orthonormal=False) @ x, expected, atol=1e-12)

    def test_dct_basis_is_orthonormal(self) -> None:
        basis = dct_matrix(16)
        np.testing.assert_allclose(basis @ basis.T, np.eye(16), atol=1e-12)
        # Truncating to the first n_mfcc rows must keep the rows orthonormal.
        truncated = dct_matrix(16, 5)
        np.testing.assert_allclose(truncated @ truncated.T, np.eye(5), atol=1e-12)

    def test_dct_of_a_constant_has_only_a_dc_term(self) -> None:
        n = 8
        coefficients = dct_matrix(n) @ np.full(n, 2.5)
        self.assertAlmostEqual(float(coefficients[0]), 2.5 * np.sqrt(n))
        np.testing.assert_allclose(coefficients[1:], 0.0, atol=1e-12)

    def test_dct_rejects_bad_sizes(self) -> None:
        with self.assertRaises(ValueError):
            dct_matrix(0)
        with self.assertRaises(ValueError):
            dct_matrix(8, 0)


class MfccTests(unittest.TestCase):
    def test_shape_and_finiteness_for_noise_silence_and_tone(self) -> None:
        for kind in ("noise", "silence", "tone"):
            with self.subTest(kind=kind):
                coefficients = mfcc(make_signal(kind), SAMPLE_RATE)
                self.assertEqual(coefficients.shape, (EXPECTED_FRAMES, 13))
                self.assertTrue(np.all(np.isfinite(coefficients)))
                self.assertEqual(coefficients.dtype, np.float32)

    def test_n_mfcc_and_n_mels_are_configurable(self) -> None:
        coefficients = mfcc(make_signal("noise"), SAMPLE_RATE, n_mfcc=20, n_mels=32)
        self.assertEqual(coefficients.shape, (EXPECTED_FRAMES, 20))
        self.assertTrue(np.all(np.isfinite(coefficients)))

    def test_silence_is_the_log_mel_floor_and_stays_finite(self) -> None:
        coefficients = mfcc(make_signal("silence"), SAMPLE_RATE)
        # log_mel_spectrogram floors the power at 1e-10, i.e. -10 in log10.
        self.assertAlmostEqual(float(coefficients[:, 0].mean()), -10.0 * np.sqrt(40.0), places=4)
        self.assertTrue(np.all(np.isfinite(coefficients)))

    def test_tone_and_noise_have_different_cepstra(self) -> None:
        tone = mfcc(make_signal("tone"), SAMPLE_RATE).mean(axis=0)
        noise = mfcc(make_signal("noise"), SAMPLE_RATE).mean(axis=0)
        self.assertGreater(float(np.max(np.abs(tone - noise))), 1e-2)

    def test_rejects_bad_n_mfcc(self) -> None:
        with self.assertRaises(ValueError):
            mfcc(make_signal("noise"), SAMPLE_RATE, n_mfcc=0)


class DeltaTests(unittest.TestCase):
    def test_delta_of_a_linear_ramp_is_its_slope(self) -> None:
        slope = 0.75
        ramp = (slope * np.arange(50, dtype=np.float64) + 4.0).reshape(-1, 1)
        for width in (1, 2, 3, 5):
            with self.subTest(width=width):
                np.testing.assert_allclose(deltas(ramp, width), slope, atol=1e-6)

    def test_delta_of_a_constant_is_zero(self) -> None:
        constant = np.full((40, 13), -3.25, dtype=np.float32)
        np.testing.assert_allclose(deltas(constant), 0.0, atol=1e-7)
        np.testing.assert_allclose(delta_delta(constant), 0.0, atol=1e-7)

    def test_delta_of_second_order_ramp_is_constant_and_not_zero(self) -> None:
        curvature = 0.02
        t = np.arange(60, dtype=np.float64)
        quadratic = (curvature * t**2).reshape(-1, 1)
        # For interior frames the regression estimator is exact: d[t] = 2*a*t.
        first = deltas(quadratic, 2)
        np.testing.assert_allclose(first[2:-2, 0], 2.0 * curvature * t[2:-2], atol=1e-6)
        # Second-order differences of a quadratic are the constant 2*a.
        second = delta_delta(quadratic, 2)
        np.testing.assert_allclose(second[4:-4, 0], 2.0 * curvature, atol=1e-6)
        self.assertGreater(float(np.max(np.abs(second))), 1e-3)

    def test_shapes_are_preserved_at_the_boundaries(self) -> None:
        features = np.arange(24, dtype=np.float32).reshape(8, 3) ** 2
        result = deltas(features)
        self.assertEqual(result.shape, features.shape)
        self.assertEqual(result.dtype, np.float32)
        one_dimensional = deltas(features[:, 0])
        self.assertEqual(one_dimensional.shape, features[:, 0].shape)

    def test_single_frame_produces_zero_delta(self) -> None:
        np.testing.assert_allclose(deltas(np.ones((1, 4), dtype=np.float32)), 0.0)

    def test_rejects_bad_width(self) -> None:
        with self.assertRaises(ValueError):
            deltas(np.zeros((5, 2), dtype=np.float32), width=0)
        with self.assertRaises(ValueError):
            deltas(np.zeros((5, 2, 2), dtype=np.float32))


class WindowVectorTests(unittest.TestCase):
    def test_length_matches_the_documented_formula(self) -> None:
        self.assertEqual(mfcc_feature_dim(), 117)
        self.assertEqual(mfcc_feature_dim(13, include_delta_delta=False), 78)
        self.assertEqual(mfcc_feature_dim(20), 180)
        self.assertEqual(
            mfcc_feature_dim(), 13 * len(AGGREGATIONS) * len(FEATURE_SETS)
        )
        for n_mfcc, include_delta_delta in ((13, True), (13, False), (20, True), (6, False)):
            vector = mfcc_window_vector(
                make_signal("noise"),
                SAMPLE_RATE,
                n_mfcc=n_mfcc,
                include_delta_delta=include_delta_delta,
            )
            self.assertEqual(
                vector.shape, (mfcc_feature_dim(n_mfcc, include_delta_delta),)
            )

    def test_default_vector_is_117_finite_float32(self) -> None:
        vector = mfcc_window_vector(make_signal("noise"), SAMPLE_RATE)
        self.assertEqual(vector.shape, (117,))
        self.assertEqual(vector.dtype, np.float32)
        self.assertTrue(np.all(np.isfinite(vector)))

    def test_silence_vector_is_finite_and_zero_variance(self) -> None:
        vector = mfcc_window_vector(make_signal("silence"), SAMPLE_RATE)
        self.assertEqual(vector.shape, (117,))
        self.assertTrue(np.all(np.isfinite(vector)))
        # Static/delta/delta-delta std blocks are (numerically) zero for a flat window.
        std_blocks = np.concatenate(
            [np.arange(offset, offset + 13) for offset in (13, 52, 91)]
        )
        self.assertLess(float(np.max(np.abs(vector[std_blocks]))), 1e-3)
        # A silent window must not produce a huge delta from the log-mel floor.
        self.assertLess(float(np.max(np.abs(vector))), 100.0)

    def test_vector_is_gain_invariant(self) -> None:
        signal = make_signal("tone")
        scaled = (3.0 * signal).astype(np.float32)
        np.testing.assert_allclose(
            mfcc_window_vector(signal, SAMPLE_RATE),
            mfcc_window_vector(scaled, SAMPLE_RATE),
            atol=1e-4,
            rtol=1e-4,
        )

    def test_rejects_empty_signal(self) -> None:
        with self.assertRaises(ValueError):
            mfcc_window_vector(np.zeros(0, dtype=np.float32), SAMPLE_RATE)


class DeterminismTests(unittest.TestCase):
    def test_mfcc_and_delta_are_bitwise_reproducible(self) -> None:
        signal = make_signal("noise")
        first = mfcc(signal, SAMPLE_RATE)
        second = mfcc(signal.copy(), SAMPLE_RATE)
        self.assertTrue(np.array_equal(first, second))
        self.assertTrue(np.array_equal(deltas(first), deltas(second)))

    def test_window_vector_is_bitwise_reproducible(self) -> None:
        signal = make_signal("tone")
        first = mfcc_window_vector(signal, SAMPLE_RATE)
        second = mfcc_window_vector(signal.copy(), SAMPLE_RATE)
        self.assertTrue(np.array_equal(first, second))


class CrossValidationSmokeTests(unittest.TestCase):
    """End-to-end: synthetic dataset -> patient-level 5-fold CV -> report.json."""

    @classmethod
    def setUpClass(cls) -> None:
        import synthetic_pcg
        import train_mfcc

        cls._temp_dir = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._temp_dir.name)
        cls.data_dir = cls.tmp / "synthetic"
        cls.out_dir = cls.tmp / "report"
        cls.report: dict = {}
        cls.error: Exception | None = None

        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                cls._run(
                    synthetic_pcg,
                    ["synthetic_pcg.py", "--out-dir", str(cls.data_dir), "--subjects", "10"],
                )
                cls._run(
                    train_mfcc,
                    [
                        "train_mfcc.py",
                        "--data-dir",
                        str(cls.data_dir),
                        "--out-dir",
                        str(cls.out_dir),
                        "--n-folds",
                        "5",
                        "--max-windows-per-record",
                        "2",
                        "--epochs",
                        "120",
                    ],
                )
            cls.report = json.loads((cls.out_dir / "report.json").read_text(encoding="utf-8"))
        except (Exception, SystemExit) as exc:  # pragma: no cover - surfaced by the tests below
            cls.error = exc
        cls.log = buffer.getvalue()

    @classmethod
    def _run(cls, module, argv: list[str]) -> None:
        original = sys.argv
        sys.argv = argv
        try:
            module.main()
        finally:
            sys.argv = original

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp_dir.cleanup()

    def setUp(self) -> None:
        if self.error is not None:
            self.fail(f"pipeline smoke test failed: {self.error!r}\n{self.log[-4000:]}")

    def test_report_files_and_feature_configuration(self) -> None:
        self.assertTrue((self.out_dir / "report.json").exists())
        self.assertTrue((self.out_dir / "report.md").exists())
        config = self.report["feature_config"]
        self.assertEqual(config["feature_count"], 117)
        self.assertTrue(config["feature_count_matches_formula"])
        self.assertEqual(config["feature_sets"], ["static", "delta", "delta_delta"])
        self.assertEqual(config["aggregations"], ["mean", "std", "p90"])
        self.assertEqual(config["n_mfcc"], 13)

    def test_pipeline_actually_consumed_the_generated_dataset(self) -> None:
        data = self.report["data"]
        self.assertEqual(data["subjects_total"], 10)
        self.assertGreater(data["recordings"], 0)
        self.assertGreater(data["windows_total"], 0)
        self.assertTrue(data["is_synthetic"])
        self.assertIn("synthetic", data["dataset_kind"].lower())

    def test_five_folds_with_finite_patient_metrics(self) -> None:
        self.assertEqual(self.report["protocol"]["folds"], 5)
        self.assertEqual(len(self.report["per_fold"]), 5)
        for fold in self.report["per_fold"]:
            self.assertTrue(np.isfinite(fold["patient"]["auroc"]))
            self.assertTrue(np.isfinite(fold["patient"]["balanced_accuracy"]))
            self.assertTrue(np.isfinite(fold["window"]["auroc"]))
            self.assertGreaterEqual(fold["threshold"], 0.0)
            self.assertLessEqual(fold["threshold"], 1.0)
            self.assertEqual(
                fold["patient"]["n"],
                fold["n_test_subjects"],
            )
        for key in ("patient_auroc", "patient_balanced_accuracy", "window_auroc"):
            self.assertTrue(np.isfinite(self.report["aggregate"][key]["mean"]))
        pooled = self.report["pooled_patient_at_0.5"]
        self.assertTrue(np.isfinite(pooled["auroc"]))
        self.assertTrue(np.isfinite(pooled["auroc_ci95"]["low"]))
        self.assertLessEqual(pooled["auroc_ci95"]["low"], pooled["auroc_ci95"]["high"])

    def test_no_patient_overlap_between_train_and_test(self) -> None:
        subject_sets = []
        for fold in self.report["per_fold"]:
            train = set(fold["train_subjects"])
            validation = set(fold["val_subjects"])
            test = set(fold["test_subjects"])
            self.assertEqual(train & test, set())
            self.assertEqual(validation & test, set())
            self.assertTrue(test)
            subject_sets.append(test)
        # Every subject is a test subject in exactly one fold.
        for index, left in enumerate(subject_sets):
            for right in subject_sets[index + 1 :]:
                self.assertEqual(left & right, set())
        union = set().union(*subject_sets)
        self.assertEqual(len(union), self.report["data"]["subjects_total"])
        self.assertEqual(self.report["patient_overlap"]["train_test_max"], 0)

    def test_markdown_reports_the_core_numbers(self) -> None:
        markdown = (self.out_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("Aggregate (mean +/- std across folds)", markdown)
        self.assertIn("Pooled patient-level", markdown)
        self.assertIn("117", markdown)
        self.assertIn("Feature vector", markdown)


if __name__ == "__main__":
    unittest.main()
