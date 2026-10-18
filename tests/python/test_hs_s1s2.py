"""Unit tests for the deterministic DSP S1/S2 detector."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "ai"))

from hs_events import S1, S2, evaluate, make_event_set
from hs_s1s2 import (
    DetectorConfig,
    _merge_close_peaks,
    detect_s1s2,
    estimate_heart_rate,
    label_events,
    label_events_sequential,
)

SAMPLE_RATE = 4000


def burst_pcg(
    duration_s: float = 10.0,
    rr_s: float = 1.0,
    s1_offset_s: float = 0.10,
    s2_offset_s: float = 0.45,
    s1_amplitude: float = 0.8,
    s2_amplitude: float = 0.45,
    noise: float = 0.005,
    seed: int = 11,
) -> tuple[np.ndarray, list[tuple[float, int]]]:
    """Damped 40/60 Hz bursts with known S1/S2 onsets."""
    n_samples = int(duration_s * SAMPLE_RATE)
    signal = np.zeros(n_samples, dtype=np.float64)
    truth: list[tuple[float, int]] = []
    beat = 0.0
    while beat + s2_offset_s < duration_s - 0.1:
        for center, amplitude, frequency, label in (
            (beat + s1_offset_s, s1_amplitude, 40.0, S1),
            (beat + s2_offset_s, s2_amplitude, 60.0, S2),
        ):
            start = int(round(center * SAMPLE_RATE))
            length = min(int(0.15 * SAMPLE_RATE), n_samples - start)
            time = np.arange(length) / SAMPLE_RATE
            signal[start : start + length] += (
                amplitude * np.exp(-time / 0.04) * np.sin(2 * np.pi * frequency * time)
            )
            truth.append((center, label))
        beat += rr_s
    rng = np.random.default_rng(seed)
    signal += noise * rng.normal(size=n_samples)
    return signal, truth


def truth_events(truth: list[tuple[float, int]]):
    return make_event_set([time for time, _ in truth], [label for _, label in truth])


class DetectionTests(unittest.TestCase):
    def test_detects_every_synthetic_beat(self) -> None:
        signal, truth = burst_pcg()
        result = detect_s1s2(signal, DetectorConfig(sample_rate=SAMPLE_RATE))
        self.assertEqual(len(truth), 20)
        self.assertGreaterEqual(len(result.events), 18)
        self.assertLessEqual(len(result.events), 22)
        report = evaluate(truth_events(truth), result.events, tolerance_s=0.1)
        self.assertGreater(report["f1_macro"], 0.95)
        self.assertLess(report["matches"]["swap_rate"], 0.05)

    def test_labels_follow_interval_rule(self) -> None:
        signal, _ = burst_pcg()
        result = detect_s1s2(signal, DetectorConfig(sample_rate=SAMPLE_RATE))
        labels = result.events.labels
        self.assertEqual(int(labels[0]), S1)
        self.assertEqual(int(labels[1]), S2)
        gaps = np.diff(result.events.times)
        # Systole (S1 -> S2) must be the shorter phase.
        self.assertLess(float(np.median(gaps[0::2])), float(np.median(gaps[1::2])))

    def test_heart_rate_matches_synthesised_rate(self) -> None:
        signal, _ = burst_pcg(rr_s=0.75)
        result = detect_s1s2(signal, DetectorConfig(sample_rate=SAMPLE_RATE))
        self.assertAlmostEqual(result.info["heart_rate_bpm"], 80.0, delta=6.0)

    def test_first_event_s2_is_relabelled(self) -> None:
        # Start the recording on S2: S2 at 0.10, S1 at 0.75, ...
        signal, _ = burst_pcg(s1_offset_s=0.75, s2_offset_s=0.10)
        result = detect_s1s2(signal, DetectorConfig(sample_rate=SAMPLE_RATE))
        self.assertGreaterEqual(len(result.events), 10)
        self.assertEqual(int(result.events.labels[0]), S2)
        self.assertEqual(int(result.events.labels[1]), S1)

    def test_silent_and_constant_signals_yield_no_events(self) -> None:
        silence = np.zeros(SAMPLE_RATE * 5, dtype=np.float64)
        constant = np.full(SAMPLE_RATE * 5, 0.3, dtype=np.float64)
        config = DetectorConfig(sample_rate=SAMPLE_RATE)
        self.assertEqual(len(detect_s1s2(silence, config).events), 0)
        self.assertEqual(len(detect_s1s2(constant, config).events), 0)

    def test_short_signal_yield_no_events(self) -> None:
        config = DetectorConfig(sample_rate=SAMPLE_RATE)
        self.assertEqual(len(detect_s1s2(np.zeros(10), config).events), 0)

    def test_detection_is_deterministic(self) -> None:
        signal, _ = burst_pcg()
        config = DetectorConfig(sample_rate=SAMPLE_RATE)
        first = detect_s1s2(signal, config)
        second = detect_s1s2(signal, config)
        np.testing.assert_array_equal(first.events.times, second.events.times)
        np.testing.assert_array_equal(first.events.labels, second.events.labels)

    def test_works_at_8khz_without_changing_band_limits(self) -> None:
        n_samples = 8000 * 6
        signal = np.zeros(n_samples)
        for beat in range(6):
            for center, frequency in ((beat * 1.0 + 0.1, 40.0), (beat * 1.0 + 0.45, 60.0)):
                start = int(center * 8000)
                length = int(0.15 * 8000)
                time = np.arange(length) / 8000
                signal[start : start + length] += (
                    0.7 * np.exp(-time / 0.04) * np.sin(2 * np.pi * frequency * time)
                )
        result = detect_s1s2(signal, DetectorConfig(sample_rate=8000))
        self.assertGreaterEqual(len(result.events), 8)


class ConfigurationTests(unittest.TestCase):
    def test_with_sample_rate_is_immutable(self) -> None:
        base = DetectorConfig()
        updated = base.with_sample_rate(8000)
        self.assertEqual(base.sample_rate, 4000)
        self.assertEqual(updated.sample_rate, 8000)

    def test_label_events_handles_single_event(self) -> None:
        labels = label_events(np.array([0.5]), np.array([1.0]))
        np.testing.assert_array_equal(labels, [S1])

    def test_heart_rate_needs_three_events(self) -> None:
        self.assertTrue(np.isnan(estimate_heart_rate(np.array([0.1, 0.5]))))
        self.assertAlmostEqual(estimate_heart_rate(np.array([0.1, 0.45, 1.10])), 60.0)


class HighRatePriorTests(unittest.TestCase):
    """The physiological prior must decide the S1/S2 orientation at high rate.

    Above ~130 bpm systole stops being the shorter interval for many patients,
    so the legacy "shorter gap is systole" rule inverts the whole recording.
    These tests pin the mechanism: same event times, different gap_prior.
    """

    RR_S = 0.40        # 150 bpm
    SYSTOLE_S = 0.21   # longer than the 0.19 s diastole

    @classmethod
    def high_rate_times(cls, beats: int = 6) -> np.ndarray:
        times: list[float] = []
        for beat in range(beats):
            start = beat * cls.RR_S
            times.extend((start, start + cls.SYSTOLE_S))
        return np.asarray(times, dtype=np.float64)

    def test_default_prior_orients_the_longer_gap_as_systole(self) -> None:
        times = self.high_rate_times()
        labels, info = label_events_sequential(times)
        self.assertTrue(info["prior_active"])
        self.assertEqual(info["labeler"], "sequential-prior")
        self.assertGreater(info["systole_prior_s"], info["diastole_prior_s"])
        expected = np.where(np.arange(len(times)) % 2 == 0, S1, S2)
        np.testing.assert_array_equal(labels, expected)

    def test_legacy_cluster_rule_inverts_the_same_recording(self) -> None:
        times = self.high_rate_times()
        labels, info = label_events_sequential(
            times, DetectorConfig(gap_prior="cluster")
        )
        self.assertFalse(info["prior_active"])
        self.assertEqual(info["labeler"], "sequential")
        np.testing.assert_array_equal(labels[:4], [S2, S1, S2, S1])

    def test_prior_is_inactive_below_the_rate_gate(self) -> None:
        times = np.array([0.20, 0.45, 1.00, 1.25, 1.80, 2.05])
        labels, info = label_events_sequential(times)
        legacy, _ = label_events_sequential(
            times, DetectorConfig(gap_prior="cluster")
        )
        np.testing.assert_array_equal(labels, legacy)
        self.assertFalse(info["prior_active"])
        self.assertEqual(info["labeler"], "sequential")

    def test_prior_min_bpm_is_configurable(self) -> None:
        times = self.high_rate_times()
        labels, info = label_events_sequential(
            times, DetectorConfig(prior_min_bpm=0.0)
        )
        self.assertTrue(info["prior_active"])
        self.assertEqual(len(labels), len(times))

    def test_model_mode_uses_the_regression_durations(self) -> None:
        times = self.high_rate_times()
        _, info = label_events_sequential(times, DetectorConfig(gap_prior="model"))
        self.assertEqual(info["labeler"], "sequential-prior")
        self.assertAlmostEqual(info["systole_est_s"], info["systole_prior_s"])
        self.assertAlmostEqual(info["diastole_est_s"], info["diastole_prior_s"])

    def test_orient_mode_keeps_the_observed_cluster_durations(self) -> None:
        times = self.high_rate_times()
        _, info = label_events_sequential(times, DetectorConfig(gap_prior="orient"))
        # The clusters are 0.19/0.21; the prior only swaps which one is S1.
        self.assertAlmostEqual(info["systole_est_s"], self.SYSTOLE_S, places=6)
        self.assertAlmostEqual(
            info["diastole_est_s"], self.RR_S - self.SYSTOLE_S, places=6
        )

    def test_candidate_b_fixed_fraction_preset(self) -> None:
        # Candidate B: S = clamp(0.32 * sqrt(RR), 0.18, 0.34).
        config = DetectorConfig(
            gap_prior="model",
            systole_prior_a=0.32,
            systole_prior_b=0.0,
            systole_prior_min_s=0.18,
            systole_prior_max_s=0.34,
        )
        times = self.high_rate_times()
        _, info = label_events_sequential(times, config)
        expected = 0.32 * float(np.sqrt(self.RR_S))
        self.assertAlmostEqual(info["systole_prior_s"], expected, places=6)

    def test_prior_is_clamped_at_both_ends(self) -> None:
        high = DetectorConfig(
            gap_prior="model",
            systole_prior_a=1.0,
            systole_prior_b=0.0,
            prior_min_bpm=0.0,
        )
        _, info = label_events_sequential(np.array([0.0, 0.6, 1.2, 1.8]), high)
        self.assertAlmostEqual(info["systole_prior_s"], 0.60)
        low = DetectorConfig(
            gap_prior="model", systole_prior_a=0.0, systole_prior_b=0.0
        )
        _, info = label_events_sequential(self.high_rate_times(), low)
        self.assertAlmostEqual(info["systole_prior_s"], 0.10)

    def test_prior_output_is_deterministic(self) -> None:
        times = self.high_rate_times()
        first, first_info = label_events_sequential(times)
        second, second_info = label_events_sequential(times)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(first_info, second_info)
        self.assertGreater(first_info["cycle_est_s"], 0.0)

    def test_high_rate_detection_is_not_inverted_end_to_end(self) -> None:
        signal, truth = burst_pcg(
            duration_s=8.0,
            rr_s=self.RR_S,
            s1_offset_s=0.10,
            s2_offset_s=0.31,
            noise=0.002,
        )
        legacy = detect_s1s2(
            signal, DetectorConfig(sample_rate=SAMPLE_RATE, gap_prior="cluster")
        )
        prior = detect_s1s2(signal, DetectorConfig(sample_rate=SAMPLE_RATE))
        self.assertGreater(legacy.info["heart_rate_bpm"], 140.0)
        legacy_report = evaluate(truth_events(truth), legacy.events, 0.1)
        prior_report = evaluate(truth_events(truth), prior.events, 0.1)
        self.assertGreater(legacy_report["matches"]["swap_rate"], 0.9)
        self.assertEqual(prior_report["matches"]["swapped"], 0)
        self.assertGreater(prior_report["f1_macro"], 0.95)
        self.assertTrue(prior.info["prior_active"])



class SequentialLabellerTests(unittest.TestCase):
    """The labeller must recover from a single spurious or missing event."""

    def test_regular_rhythm_alternates(self) -> None:
        times = np.array([0.20, 0.45, 1.00, 1.25, 1.80, 2.05])
        labels, info = label_events_sequential(times)
        np.testing.assert_array_equal(labels, [S1, S2, S1, S2, S1, S2])
        self.assertEqual(info["labeler"], "sequential")
        self.assertAlmostEqual(info["systole_est_s"], 0.25, places=2)

    def test_missing_beat_is_absorbed_not_inverted(self) -> None:
        # S2 of the first beat is missing: S1 then S1, then the rhythm resumes.
        times = np.array([0.20, 1.00, 1.25, 1.80, 2.05])
        labels, _ = label_events_sequential(times)
        np.testing.assert_array_equal(labels, [S1, S1, S2, S1, S2])

    def test_recording_starting_on_s2_is_recognised(self) -> None:
        # First gap is long, so the first event must be labelled S2.
        times = np.array([0.20, 0.85, 1.10, 1.75, 2.00])
        labels, _ = label_events_sequential(times)
        np.testing.assert_array_equal(labels, [S2, S1, S2, S1, S2])

    def test_indistinguishable_gaps_fall_back(self) -> None:
        times = np.linspace(0.0, 3.0, 13)  # every gap identical
        labels, info = label_events_sequential(times)
        self.assertEqual(info["labeler"], "sequential-fallback")
        self.assertEqual(len(labels), 13)


class SpuriousPeakMergeTests(unittest.TestCase):
    def test_weaker_of_two_close_peaks_is_dropped(self) -> None:
        envelope = np.zeros(400)
        envelope[100] = 0.4
        envelope[125] = 0.9
        merged = _merge_close_peaks(np.array([100, 125]), envelope, 60)
        np.testing.assert_array_equal(merged, [125])

    def test_far_peaks_are_kept(self) -> None:
        envelope = np.zeros(400)
        envelope[100] = 0.4
        envelope[300] = 0.9
        merged = _merge_close_peaks(np.array([100, 300]), envelope, 60)
        np.testing.assert_array_equal(merged, [100, 300])

    def test_chained_close_peaks_collapse_to_the_strongest(self) -> None:
        envelope = np.zeros(500)
        envelope[100] = 0.3
        envelope[140] = 0.5
        envelope[180] = 0.95
        merged = _merge_close_peaks(np.array([100, 140, 180]), envelope, 60)
        np.testing.assert_array_equal(merged, [180])

    def test_spurious_click_does_not_invert_end_to_end(self) -> None:
        signal, truth = burst_pcg(duration_s=8.0, rr_s=1.0, noise=0.002)
        # Add a short click 80 ms after each S1, i.e. inside the same sound.
        for time, label in truth:
            if label == S1:
                start = int((time + 0.08) * SAMPLE_RATE)
                length = int(0.01 * SAMPLE_RATE)
                signal[start : start + length] += 0.6 * np.hanning(length)
        result = detect_s1s2(signal, DetectorConfig(sample_rate=SAMPLE_RATE))
        report = evaluate(truth_events(truth), result.events, tolerance_s=0.1)
        self.assertEqual(report["matches"]["swapped"], 0)
        self.assertGreater(report["f1_macro"], 0.9)


if __name__ == "__main__":
    unittest.main()
