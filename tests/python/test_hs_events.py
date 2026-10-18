"""Unit tests for event-level S1/S2 evaluation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "ai"))

from hs_events import (
    S1,
    S2,
    add_counts,
    counts,
    describe_errors,
    evaluate,
    make_event_set,
    match_events,
    metrics_from_counts,
    segments_to_events,
    summarize,
)
from hs_segments import annotated_intervals, mask_times


class SegmentConversionTests(unittest.TestCase):
    def test_only_s1_and_s2_segments_become_events(self) -> None:
        segments = np.array(
            [
                [0.00, 0.10, 1],
                [0.10, 0.40, 2],
                [0.40, 0.50, 3],
                [0.50, 0.90, 4],
            ],
            dtype=float,
        )
        events = segments_to_events(segments)
        self.assertEqual(len(events), 2)
        np.testing.assert_allclose(events.times, [0.05, 0.45])
        np.testing.assert_array_equal(events.labels, [S1, S2])

    def test_empty_segments_yield_empty_events(self) -> None:
        self.assertEqual(len(segments_to_events(np.zeros((0, 3)))), 0)


class MatchingTests(unittest.TestCase):
    def test_identical_events_match_perfectly(self) -> None:
        events = make_event_set([0.2, 0.55, 1.2, 1.55], [S1, S2, S1, S2])
        report = evaluate(events, events)
        self.assertEqual(report["matches"]["total"], 4)
        self.assertEqual(report["matches"]["swapped"], 0)
        self.assertAlmostEqual(report["per_class"]["s1"]["f1"], 1.0)
        self.assertAlmostEqual(report["per_class"]["s2"]["f1"], 1.0)
        self.assertAlmostEqual(report["boundary_error_s"]["max"], 0.0)

    def test_tolerance_boundary(self) -> None:
        ref = make_event_set([0.5], [S1])
        inside = make_event_set([0.55], [S1])
        outside = make_event_set([0.75], [S1])
        self.assertEqual(len(match_events(ref, inside, 0.1)), 1)
        self.assertEqual(len(match_events(ref, outside, 0.1)), 0)

    def test_matching_is_one_to_one(self) -> None:
        ref = make_event_set([0.50, 0.52], [S1, S1])
        hyp = make_event_set([0.51], [S1])
        self.assertEqual(len(match_events(ref, hyp, 0.2)), 1)

    def test_swapped_classes_are_counted(self) -> None:
        ref = make_event_set([0.2, 0.55], [S1, S2])
        hyp = make_event_set([0.2, 0.55], [S2, S1])
        report = evaluate(ref, hyp)
        self.assertEqual(report["matches"]["swapped"], 2)
        self.assertAlmostEqual(report["matches"]["swap_rate"], 1.0)
        # A swapped match is a false positive for the detected class and a
        # false negative for the reference class.
        self.assertEqual(report["per_class"]["s1"]["tp"], 0)
        self.assertEqual(report["per_class"]["s1"]["fp"], 1)
        self.assertEqual(report["per_class"]["s1"]["fn"], 1)

    def test_missing_and_extra_events(self) -> None:
        ref = make_event_set([0.2, 0.55], [S1, S2])
        hyp = make_event_set([0.2, 1.10], [S1, S2])
        report = evaluate(ref, hyp)
        self.assertEqual(report["per_class"]["s1"]["tp"], 1)
        self.assertEqual(report["per_class"]["s2"]["tp"], 0)
        self.assertEqual(report["per_class"]["s2"]["fp"], 1)
        self.assertEqual(report["per_class"]["s2"]["fn"], 1)
        self.assertAlmostEqual(report["per_class"]["s1"]["f1"], 1.0)

    def test_boundary_error_statistics(self) -> None:
        ref = make_event_set([1.0, 2.0], [S1, S2])
        hyp = make_event_set([1.02, 1.94], [S1, S2])
        report = evaluate(ref, hyp)
        self.assertAlmostEqual(report["boundary_error_s"]["median"], 0.04)
        self.assertAlmostEqual(report["boundary_error_s"]["max"], 0.06)

    def test_empty_detection_has_zero_recall(self) -> None:
        ref = make_event_set([0.2, 0.55], [S1, S2])
        report = evaluate(ref, make_event_set([], []))
        self.assertEqual(report["per_class"]["s1"]["recall"], 0.0)
        self.assertEqual(report["per_class"]["s2"]["recall"], 0.0)


class AggregationTests(unittest.TestCase):
    def test_counts_round_trip_to_metrics(self) -> None:
        ref = make_event_set([0.2, 0.55, 1.2, 1.55], [S1, S2, S1, S2])
        hyp = make_event_set([0.2, 0.60, 1.2], [S1, S2, S1])
        report = evaluate(ref, hyp)
        metrics = metrics_from_counts(counts(report))
        self.assertAlmostEqual(metrics["f1_macro"], report["f1_macro"])
        self.assertAlmostEqual(
            metrics["per_class"]["s2"]["f1"], report["per_class"]["s2"]["f1"]
        )

    def test_add_counts_ignores_patient_id(self) -> None:
        left = {"tp_s1": 1, "fp_s1": 0, "fn_s1": 0, "patient_id": "90000"}
        right = {"tp_s1": 2, "fp_s1": 1, "fn_s1": 0, "patient_id": "90001"}
        total = add_counts(left, right)
        self.assertEqual(total["tp_s1"], 3)
        self.assertEqual(total["fp_s1"], 1)
        self.assertNotIn("patient_id", total)

    def test_describe_errors_handles_empty_input(self) -> None:
        stats = describe_errors([])
        self.assertEqual(stats["n"], 0)
        self.assertTrue(np.isnan(stats["median"]))
        self.assertTrue(np.isnan(stats["p95"]))

    def test_describe_errors_reports_percentiles(self) -> None:
        stats = describe_errors([0.0, 0.01, 0.02, 0.03])
        self.assertEqual(stats["n"], 4)
        self.assertAlmostEqual(stats["median"], 0.015)
        self.assertAlmostEqual(stats["max"], 0.03)

    def test_summarize_computes_boundary_without_patients(self) -> None:
        summary = summarize([], [[0.01, 0.02], [0.03]])
        self.assertEqual(summary["patients"], 0)
        self.assertEqual(summary["pooled"]["boundary_error_s"]["n"], 3)

    def test_pooled_and_macro_summary(self) -> None:
        perfect = make_event_set([0.2, 0.55], [S1, S2])
        halves = make_event_set([0.2], [S1])
        rows = []
        for name, hyp in (("a", perfect), ("b", halves)):
            report = evaluate(perfect, hyp)
            row = counts(report)
            row["patient_id"] = name
            rows.append(row)
        summary = summarize(rows)
        self.assertEqual(summary["patients"], 2)
        self.assertAlmostEqual(summary["macro"]["f1_s1"]["mean"], 1.0)
        self.assertAlmostEqual(summary["pooled"]["per_class"]["s2"]["recall"], 0.5)


if __name__ == "__main__":
    unittest.main()


class AnnotationMaskTests(unittest.TestCase):
    """CirCor label 0 means unannotated/low quality; events there have no reference."""

    def test_label_zero_intervals_are_excluded(self) -> None:
        segments = np.array(
            [
                [0.00, 10.00, 0],
                [0.50, 0.60, 1],
                [0.60, 0.70, 2],
                [0.70, 0.80, 3],
                [0.80, 1.20, 4],
            ],
            dtype=float,
        )
        intervals = annotated_intervals(segments, 10.0)
        self.assertEqual(len(intervals), 1)
        self.assertAlmostEqual(intervals[0][0], 0.50)
        self.assertAlmostEqual(intervals[0][1], 1.20)

    def test_overlapping_intervals_merge(self) -> None:
        segments = np.array(
            [[0.0, 0.5, 1], [0.4, 0.8, 3], [1.0, 1.2, 3]], dtype=float
        )
        intervals = annotated_intervals(segments)
        self.assertEqual(len(intervals), 2)
        self.assertAlmostEqual(intervals[0][1], 0.8)
        self.assertAlmostEqual(intervals[1][0], 1.0)

    def test_intervals_clamped_to_duration(self) -> None:
        segments = np.array([[8.0, 12.0, 1]], dtype=float)
        intervals = annotated_intervals(segments, 10.0)
        self.assertAlmostEqual(intervals[0][1], 10.0)

    def test_mask_times_keeps_only_annotated_events(self) -> None:
        intervals = [(0.5, 1.2), (2.0, 3.0)]
        mask = mask_times(np.array([0.1, 0.5, 0.9, 1.5, 2.5]), intervals)
        np.testing.assert_array_equal(mask, [False, True, True, False, True])

    def test_mask_on_synthetic_style_segments_is_nearly_total(self) -> None:
        # Contiguous 1-4 segments must cover the annotated span end to end.
        segments = np.array(
            [[0.0, 0.1, 1], [0.1, 0.3, 2], [0.3, 0.4, 3], [0.4, 0.9, 4]], dtype=float
        )
        mask = mask_times(np.array([0.05, 0.35, 0.65]), annotated_intervals(segments))
        np.testing.assert_array_equal(mask, [True, True, True])


class HeartRateReportTests(unittest.TestCase):
    """The heart-rate evaluation helper must stay well-defined on edge cases."""

    def test_empty_error_list(self) -> None:
        from evaluate_heart_rate import summarize_errors

        self.assertEqual(summarize_errors([]), {"n": 0})

    def test_error_summary_statistics(self) -> None:
        from evaluate_heart_rate import summarize_errors

        stats = summarize_errors([0.0, 2.0, -4.0, 10.0])
        self.assertEqual(stats["n"], 4)
        self.assertAlmostEqual(stats["mean_abs_bpm"], 4.0)
        self.assertAlmostEqual(stats["median_abs_bpm"], 3.0)
        self.assertAlmostEqual(stats["max_abs_bpm"], 10.0)
        self.assertAlmostEqual(stats["within_5_bpm"], 0.75)
        self.assertAlmostEqual(stats["within_10_bpm"], 1.0)
