"""Tests for the workpoint / calibration analysis (``tools/ai/workpoint_analysis.py``).

The analysis exists to answer one question a supervisor will ask: *what does the
threshold mean clinically?*  That makes three properties non-negotiable, and
these tests pin them:

1. **Format** - the probability dump has exactly the columns the analysis reads,
   rows are sorted, and a float survives the CSV round trip bit-for-bit.
2. **No test leakage** - changing a test-fold probability or label must not move
   a threshold or a Platt coefficient, because those are fitted on the
   validation split only.
3. **Honest reporting** - a specificity floor that can only be met by never
   alerting is reported as unmet, a metric with a zero denominator is ``nan``
   rather than 0, and the analysis refuses a dump whose split fingerprint is not
   the frozen protocol.

The module is deliberately **torch-free**: CI installs NumPy only, and every
function tested here is NumPy-only.  ``cross_validate`` is imported for the dump
writer (its own heavy dependency, ``train_cnn``, is imported lazily and only for
``--model cnn``).
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
AI_DIR = REPO_ROOT / "tools" / "ai"
PYTHON = sys.executable

sys.path.insert(0, str(AI_DIR))

import cross_validate
import workpoint_analysis as wp
from hs_metrics import best_threshold, binary_metrics
from hs_splits import load_splits, splits_fingerprint, write_splits

SPLIT_ORDER = {"val": 0, "test": 1}


def write_dump(path, rows) -> Path:
    """Write a dump the way the training scripts do: header, sorted rows, repr floats.

    Deliberately independent of ``cross_validate.write_probability_dump`` so a
    change in the writer has to be a change in the format, not something both
    sides make together.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(list(wp.PROBABILITY_FIELDS))
        for fold, split, subject, label, probability in sorted(
            rows, key=lambda row: (row[0], SPLIT_ORDER[row[1]], row[2])
        ):
            writer.writerow(
                [int(fold), split, subject, int(label), repr(float(probability))]
            )
    return path


def build_rows(
    n_folds: int = 5,
    n_per_fold: int = 30,
    n_val_per_fold: int = 16,
    seed: int = 11,
    signal: float = 1.3,
):
    """A small whole-cohort dump: every patient is test in exactly one fold.

    Returns ``(rows, labels, folds)`` where ``rows`` are
    ``(fold, split, subject, label, probability)`` tuples.  Validation patients of
    fold ``k`` are drawn from the *other* folds, exactly as the frozen protocol
    does, so the dump satisfies the same structural invariants.
    """
    rng = np.random.default_rng(seed)
    subjects: list[str] = []
    labels: dict[str, int] = {}
    folds: dict[str, int] = {}
    for fold in range(n_folds):
        for index in range(n_per_fold):
            subject = f"P{fold:02d}{index:03d}"
            subjects.append(subject)
            labels[subject] = int(index % 2 == 0)
            folds[subject] = fold

    rows: list[tuple] = []
    for fold in range(n_folds):
        test_subjects = [subject for subject in subjects if folds[subject] == fold]
        validation: list[str] = []
        for label in (0, 1):
            pool = sorted(
                subject
                for subject in subjects
                if folds[subject] != fold and labels[subject] == label
            )
            rng.shuffle(pool)
            validation.extend(pool[: n_val_per_fold // 2])
        for split, group in (("val", validation), ("test", test_subjects)):
            for subject in group:
                centre = signal if labels[subject] == 1 else -signal
                latent = centre + float(rng.normal(0.0, 1.0))
                probability = float(1.0 / (1.0 + np.exp(-latent)))
                rows.append((fold, split, subject, labels[subject], probability))
    return rows, labels, folds


class DumpFormatTests(unittest.TestCase):
    """The dump is the only interface between training and analysis."""

    def test_header_is_the_shared_contract(self):
        self.assertEqual(
            wp.PROBABILITY_FIELDS, ("fold", "split", "subject_id", "label", "probability")
        )
        self.assertEqual(wp.PROBABILITY_FIELDS, cross_validate.PROBABILITY_FIELDS)

    def test_writer_sorts_rows_and_round_trips_floats(self):
        rows = [
            {
                "fold": 1,
                "split": "test",
                "subject_id": "B",
                "label": 1,
                "probability": 0.1 + 0.2,
            },
            {"fold": 0, "split": "test", "subject_id": "C", "label": 0, "probability": 0.5},
            {"fold": 0, "split": "val", "subject_id": "A", "label": 1, "probability": 1 / 3},
            {
                "fold": 0,
                "split": "test",
                "subject_id": "A",
                "label": 0,
                "probability": 2**-20,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = cross_validate.write_probability_dump(
                Path(directory) / "sub" / "dump.csv", rows
            )
            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], ",".join(wp.PROBABILITY_FIELDS))
            # fold, then val before test, then subject_id
            self.assertEqual(
                [line.split(",")[:3] for line in lines[1:]],
                [
                    ["0", "val", "A"],
                    ["0", "test", "A"],
                    ["0", "test", "C"],
                    ["1", "test", "B"],
                ],
            )
            data = wp.read_probability_dump(target)
            for row in rows:
                index = np.flatnonzero(
                    (data["fold"] == row["fold"])
                    & (data["split"] == row["split"])
                    & (data["subject"] == row["subject_id"])
                )
                self.assertEqual(len(index), 1)
                self.assertEqual(
                    float(data["probability"][index[0]]), float(row["probability"])
                )

    def test_writer_is_byte_stable(self):
        rows, _, _ = build_rows(n_folds=3, n_per_fold=8, n_val_per_fold=4)
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "a.csv"
            second = Path(directory) / "b.csv"
            write_dump(first, rows)
            write_dump(second, rows)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_writer_rejects_unknown_split(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                cross_validate.write_probability_dump(
                    Path(directory) / "x.csv",
                    [
                        {
                            "fold": 0,
                            "split": "train",
                            "subject_id": "A",
                            "label": 0,
                            "probability": 0.5,
                        }
                    ],
                )

    def test_reader_rejects_malformed_dumps(self):
        base = "fold,split,subject_id,label,probability\n0,val,A,1,0.5\n"
        cases = {
            "header": "fold,split,subject,label,probability\n0,val,A,1,0.5\n",
            "split": base.replace("0,val", "0,train"),
            "label": base.replace(",1,0.5", ",2,0.5"),
            "range": base.replace(",0.5", ",1.5"),
            "duplicate": base + "0,val,A,1,0.4\n",
            "fields": "fold,split,subject_id,label,probability\n0,val,A,1\n",
            "numeric": base.replace(",0.5", ",abc"),
            "empty": "fold,split,subject_id,label,probability\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, text in cases.items():
                target = Path(directory) / f"{name}.csv"
                target.write_text(text, encoding="utf-8")
                with self.subTest(name=name):
                    with self.assertRaises(SystemExit):
                        wp.read_probability_dump(target)

    def test_reader_reports_a_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                wp.read_probability_dump(Path(directory) / "absent.csv")

    def test_arm_spec_parsing_keeps_order_and_rejects_bad_input(self):
        specs = wp.parse_arm_specs(["logreg=/tmp/a.csv", "cnn=/tmp/b.csv"])
        self.assertEqual([arm for arm, _ in specs], ["logreg", "cnn"])
        self.assertEqual([path.name for _, path in specs], ["a.csv", "b.csv"])
        for bad in ("logreg", "=x.csv", "logreg=", "   =   "):
            with self.subTest(value=bad):
                with self.assertRaises(SystemExit):
                    wp.parse_arm_specs([bad])
        with self.assertRaises(SystemExit):
            wp.parse_arm_specs(["a=/tmp/x.csv", "a=/tmp/y.csv"])


class MetricTests(unittest.TestCase):
    def test_metrics_agree_with_hs_metrics(self):
        rng = np.random.default_rng(3)
        y = (rng.random(200) < 0.4).astype(int)
        p = np.clip(y * 0.4 + rng.random(200) * 0.6, 0.0, 1.0)
        for threshold in (0.2, 0.5, 0.7):
            mine = wp.threshold_metrics(y, p, threshold)
            reference = binary_metrics(y, p, threshold)
            for field in ("sensitivity", "specificity", "f1", "balanced_accuracy"):
                self.assertAlmostEqual(mine[field], reference[field], places=12)
            self.assertAlmostEqual(mine["ppv"], reference["precision"], places=12)
            self.assertEqual(mine["tp"], reference["confusion"]["tp"])
            self.assertEqual(mine["tn"], reference["confusion"]["tn"])
            self.assertEqual(mine["fp"], reference["confusion"]["fp"])
            self.assertEqual(mine["fn"], reference["confusion"]["fn"])

    def test_zero_denominators_are_nan_not_zero(self):
        # Nobody predicted positive: PPV is undefined, specificity is still defined.
        metrics = wp.metrics_from_counts(tp=0, tn=10, fp=0, fn=2)
        self.assertTrue(np.isnan(metrics["ppv"]))
        self.assertEqual(metrics["specificity"], 1.0)
        self.assertEqual(metrics["npv"], 10 / 12)
        # Every prediction is a true positive: NPV is undefined (nobody said no).
        metrics = wp.metrics_from_counts(tp=5, tn=0, fp=0, fn=0)
        self.assertTrue(np.isnan(metrics["npv"]))
        self.assertEqual(metrics["ppv"], 1.0)
        self.assertEqual(metrics["sensitivity"], 1.0)

    def test_bootstrap_is_seeded_and_respects_n_boot(self):
        rng = np.random.default_rng(5)
        y = (rng.random(120) < 0.45).astype(int)
        predicted = rng.random(120) < 0.5
        first = wp.bootstrap_metric_table(y, predicted, 200, 42)
        second = wp.bootstrap_metric_table(y, predicted, 200, 42)
        self.assertEqual(first, second)
        self.assertLessEqual(first["n_boot"], 200)
        self.assertGreater(first["n_boot"], 0)
        for field in wp.METRIC_FIELDS:
            self.assertLessEqual(
                first["ci95"][field]["low"], first["ci95"][field]["high"]
            )

    def test_bootstrap_of_a_single_class_returns_no_interval(self):
        result = wp.bootstrap_metric_table(np.ones(20, dtype=int), np.ones(20, bool), 50, 0)
        self.assertEqual(result["n_boot"], 0)
        self.assertTrue(np.isnan(result["ci95"]["sensitivity"]["low"]))


class ThresholdSelectionTests(unittest.TestCase):
    """The constraint is the clinical contract; these pin what it selects."""

    # Five clear positives, five borderline ones, and negatives interleaved so
    # that buying the last five positives costs more specificity than any
    # screening floor allows.
    POSITIVE = [0.9] * 5 + [0.6] * 5
    NEGATIVE = [0.7] * 3 + [0.5] * 3 + [0.1] * 4

    def val_set(self):
        y = np.array([1] * len(self.POSITIVE) + [0] * len(self.NEGATIVE))
        p = np.array(self.POSITIVE + self.NEGATIVE)
        return y, p

    def test_w1_trades_sensitivity_for_the_specificity_floor(self):
        y, p = self.val_set()
        selection = wp.select_threshold(y, p, 0.80)
        self.assertTrue(selection["feasible"])
        self.assertAlmostEqual(selection["threshold"], 0.9)
        self.assertGreaterEqual(selection["val"]["specificity"], 0.80 - 1e-12)
        self.assertAlmostEqual(selection["val"]["sensitivity"], 0.5)
        # Maximal sensitivity among everything that meets the floor.
        for threshold in wp.candidate_thresholds(p):
            metrics = wp.threshold_metrics(y, p, threshold)
            if metrics["specificity"] >= 0.80 - 1e-12:
                self.assertLessEqual(metrics["sensitivity"], 0.5 + 1e-12)

    def test_w2_meets_its_own_lower_floor_and_therefore_flags_more(self):
        y, p = self.val_set()
        relaxed = wp.select_threshold(y, p, 0.70)
        self.assertTrue(relaxed["feasible"])
        self.assertAlmostEqual(relaxed["threshold"], 0.6)
        self.assertAlmostEqual(relaxed["val"]["specificity"], 0.70)
        self.assertAlmostEqual(relaxed["val"]["sensitivity"], 1.0)
        strict = wp.select_threshold(y, p, 0.80)
        # A lower specificity floor can only lower (or keep) the threshold: it
        # buys the extra sensitivity by flagging more patients.
        self.assertLess(relaxed["threshold"], strict["threshold"])

    def test_w3_is_the_current_behaviour(self):
        y, p = self.val_set()
        selection = wp.select_threshold(y, p, None)
        reference_threshold, reference_score = best_threshold(y, p)
        self.assertAlmostEqual(selection["threshold"], reference_threshold, places=12)
        self.assertAlmostEqual(
            selection["val"]["balanced_accuracy"], reference_score, places=12
        )
        self.assertAlmostEqual(selection["threshold"], 0.6)
        # W3's balanced-accuracy optimum violates the screening floor: this is
        # exactly why W1 exists.
        self.assertLess(selection["val"]["specificity"], 0.80)

    def test_w3_matches_hs_metrics_on_a_coarse_probability_grid(self):
        rng = np.random.default_rng(17)
        y = (rng.random(160) < 0.5).astype(int)
        p = np.round(rng.random(160) * 20) / 20.0
        mine = wp.select_threshold(y, p, None)["threshold"]
        reference, _ = best_threshold(y, p)
        self.assertAlmostEqual(mine, reference, places=9)

    def test_infeasible_floor_is_reported_not_hidden(self):
        # Every threshold that alerts somebody breaks a 0.90 floor, so the only
        # "feasible" point flags nobody - which must not count as success.
        y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
        p = np.array([0.5, 0.4, 0.6, 0.6, 0.45, 0.45, 0.45, 0.3, 0.3, 0.3])
        selection = wp.select_threshold(y, p, 0.90)
        self.assertFalse(selection["feasible"])
        self.assertEqual(selection["val"]["sensitivity"], 0.0)
        self.assertGreaterEqual(selection["val"]["specificity"], 0.90)

    def test_candidates_cover_every_operating_point(self):
        candidates = wp.candidate_thresholds([0.1, 0.1, 0.4, 0.9])
        np.testing.assert_allclose(candidates, [0.1, 0.4, 0.9, 1.0])

    def test_test_rows_do_not_influence_the_choice(self):
        """The hard rule: selection sees validation rows only."""
        rows, _, _ = build_rows(seed=23)
        perturbed = [
            (fold, split, subject, label, probability if split == "val" else 1.0 - probability * 0.5)
            for fold, split, subject, label, probability in rows
        ]
        with tempfile.TemporaryDirectory() as directory:
            first = wp.analyse_arm("a", write_dump(Path(directory) / "a.csv", rows), 40, 7)
            second = wp.analyse_arm(
                "b", write_dump(Path(directory) / "b.csv", perturbed), 40, 7
            )
        for left, right in zip(first["calibration"]["per_fold"], second["calibration"]["per_fold"]):
            self.assertEqual(left["platt"]["slope"], right["platt"]["slope"])
            self.assertEqual(left["platt"]["intercept"], right["platt"]["intercept"])
            for representation in wp.REPRESENTATIONS:
                for constraint in wp.CONSTRAINTS:
                    cid = constraint["id"]
                    self.assertEqual(
                        left["workpoints"][representation][cid]["threshold"],
                        right["workpoints"][representation][cid]["threshold"],
                    )
        # ... while the evaluated test metrics do move, so the test is not vacuous.
        self.assertNotEqual(
            first["workpoints"]["calibrated"]["W1"]["pooled_oof"]["metrics"]["sensitivity"],
            second["workpoints"]["calibrated"]["W1"]["pooled_oof"]["metrics"]["sensitivity"],
        )

    def test_fold_analysis_ignores_test_labels_for_selection(self):
        """Same fold, opposite test outcomes: the thresholds must not move."""
        rows, _, _ = build_rows(seed=29)
        rng = np.random.default_rng(71)
        val_y = (rng.random(40) < 0.5).astype(int)
        val_p = np.clip(val_y * 0.3 + rng.random(40) * 0.7, 0.0, 1.0)
        test_p = np.clip(rng.random(40) * 0.9 + 0.05, 0.0, 1.0)
        test_y = (rng.random(40) < 0.5).astype(int)
        first = wp.analyse_fold(0, val_y, val_p, test_y, test_p)
        second = wp.analyse_fold(0, val_y, val_p, 1 - test_y, test_p)
        self.assertEqual(first["platt"]["slope"], second["platt"]["slope"])
        for representation in wp.REPRESENTATIONS:
            for constraint in wp.CONSTRAINTS:
                cid = constraint["id"]
                self.assertEqual(
                    first["workpoints"][representation][cid]["threshold"],
                    second["workpoints"][representation][cid]["threshold"],
                )
        self.assertNotEqual(first["brier"]["raw"], second["brier"]["raw"])


class CalibrationTests(unittest.TestCase):
    def _sample(self, overconfidence: float, n: int = 20000, seed: int = 31):
        rng = np.random.default_rng(seed)
        latent = rng.normal(0.0, 1.5, n)
        outcome = (rng.random(n) < 1.0 / (1.0 + np.exp(-latent))).astype(int)
        reported = 1.0 / (1.0 + np.exp(-overconfidence * latent))
        return outcome, reported

    def test_platt_is_near_identity_when_the_arm_is_already_calibrated(self):
        y, p = self._sample(1.0)
        model = wp.fit_platt(y, p)
        self.assertAlmostEqual(model["slope"], 1.0, delta=0.08)
        self.assertAlmostEqual(model["intercept"], 0.0, delta=0.08)
        self.assertTrue(model["converged"])

    def test_platt_recovers_the_overconfidence_factor(self):
        y, p = self._sample(2.0)
        model = wp.fit_platt(y, p)
        self.assertAlmostEqual(model["slope"], 0.5, delta=0.05)
        raw = wp.brier_score(y, p)
        calibrated = wp.brier_score(y, wp.apply_platt(model, p)["probability"])
        self.assertLess(calibrated, raw)

    def test_platt_clipping_keeps_saturated_probabilities_finite(self):
        y = np.array([0, 1, 0, 1, 0, 1, 1, 0])
        p = np.array([0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0])
        model = wp.fit_platt(y, p)
        self.assertTrue(np.isfinite(model["slope"]))
        self.assertTrue(np.isfinite(model["intercept"]))
        self.assertEqual(model["n_val_clipped"], 8)
        calibrated = wp.apply_platt(model, p)["probability"]
        self.assertTrue(np.all(np.isfinite(calibrated)))

    def test_calibration_slope_is_one_for_a_perfectly_calibrated_map(self):
        y, p = self._sample(1.0, n=8000, seed=37)
        diagnostics = wp.fit_logistic(wp.logit(p), y)
        self.assertAlmostEqual(diagnostics["slope"], 1.0, delta=0.12)
        self.assertAlmostEqual(diagnostics["intercept"], 0.0, delta=0.10)

    def test_reliability_table_covers_every_patient_and_ece_is_small_when_calibrated(self):
        y, p = self._sample(1.0, n=5000, seed=41)
        table = wp.reliability_table(y, p)
        self.assertEqual(len(table), wp.RELIABILITY_BINS)
        self.assertEqual(sum(row["n"] for row in table), len(y))
        self.assertTrue(all(row["min_predicted"] <= row["max_predicted"] for row in table))
        self.assertLess(wp.expected_calibration_error(table, len(y)), 0.02)

    def test_reliability_table_is_empty_for_a_tiny_pool(self):
        self.assertEqual(wp.reliability_table(np.array([0, 1, 0]), np.array([0.2, 0.8, 0.4])), [])

    def test_apply_platt_is_monotone_for_a_positive_slope(self):
        model = {"slope": 1.7, "intercept": -0.3}
        probabilities = np.linspace(0.01, 0.99, 50)
        calibrated = wp.apply_platt(model, probabilities)["probability"]
        self.assertTrue(np.all(np.diff(calibrated) > 0))


class DecisionCurveTests(unittest.TestCase):
    def test_net_benefit_matches_the_hand_computation(self):
        y = np.array([1, 1, 0, 0, 0, 0])
        predicted = np.array([1, 0, 1, 0, 0, 0], dtype=bool)
        # TP=1, FP=1, n=6, pt=0.25 -> 1/6 - 1/6 * (0.25/0.75)
        expected = 1 / 6 - 1 / 6 * (0.25 / 0.75)
        self.assertAlmostEqual(wp.net_benefit(y, predicted, 0.25), expected, places=12)
        self.assertAlmostEqual(wp.decision_metrics(y, predicted)["tp"], 1)

    def test_treat_all_and_treat_none_baselines(self):
        y = np.array([1, 1, 0, 0, 0, 0])
        prevalence = 2 / 6
        self.assertAlmostEqual(wp.treat_all_net_benefit(y, 0.2), prevalence - (1 - prevalence) * 0.25, places=12)
        for threshold in (0.1, 0.3, 0.5):
            self.assertEqual(
                wp.decision_curve(y, np.array([0.9, 0.8, 0.1, 0.1, 0.1, 0.1]), [threshold])[0][
                    "treat_none"
                ],
                0.0,
            )

    def test_a_perfect_model_earns_exactly_the_prevalence(self):
        # Separated at every grid point, so TP = 2, FP = 0 at each of them.
        y = np.array([1, 1, 0, 0])
        probabilities = np.array([0.99, 0.98, 0.01, 0.005])
        for row in wp.decision_curve(y, probabilities, [0.05, 0.25, 0.5]):
            self.assertAlmostEqual(row["net_benefit"], 0.5, places=12)
            self.assertTrue(np.isfinite(row["treat_all"]))

    def test_grid_is_the_protocol_range(self):
        self.assertEqual(list(wp.DCA_GRID), [round(0.05 * step, 2) for step in range(1, 11)])


class ProtocolTests(unittest.TestCase):
    def test_cohort_checks_count_patients_and_prevalence(self):
        rows, labels, _ = build_rows()
        with tempfile.TemporaryDirectory() as directory:
            data = wp.read_probability_dump(write_dump(Path(directory) / "d.csv", rows))
            cohort = wp.cohort_checks(data)
        self.assertEqual(cohort["n_patients"], len(labels))
        self.assertEqual(cohort["n_folds"], 5)
        self.assertAlmostEqual(cohort["prevalence"], float(np.mean(list(labels.values()))))
        self.assertEqual(
            cohort["n_test_rows"], len(labels), "every patient is test exactly once"
        )

    def test_fingerprint_from_the_dump_reproduces_the_frozen_file(self):
        rows, labels, folds = build_rows(seed=13)
        with tempfile.TemporaryDirectory() as directory:
            dump = write_dump(Path(directory) / "d.csv", rows)
            report = wp.analyse_arm("arm", dump, 20, 1)
            splits_path = write_splits(
                Path(directory) / "splits.csv",
                folds,
                labels,
                {"dataset": "unit-test", "n_folds": 5},
            )
            expected = splits_fingerprint(load_splits(splits_path))
        self.assertEqual(report["cohort"]["splits_fingerprint"], expected)

    def test_a_patient_in_two_test_folds_is_rejected(self):
        rows, _, _ = build_rows(n_folds=3, n_per_fold=6, n_val_per_fold=2)
        duplicated = list(rows)
        fold, split, subject, label, probability = next(
            row for row in rows if row[1] == "test"
        )
        duplicated.append((1 if fold != 1 else 2, "test", subject, label, probability))
        with tempfile.TemporaryDirectory() as directory:
            dump = write_dump(Path(directory) / "d.csv", duplicated)
            with self.assertRaises(SystemExit):
                wp.analyse_arm("arm", dump, 10, 1)

    def test_a_patient_in_both_splits_of_one_fold_is_rejected(self):
        rows, _, _ = build_rows(n_folds=3, n_per_fold=6, n_val_per_fold=2)
        fold, _, subject, label, probability = next(row for row in rows if row[1] == "test")
        rows = rows + [(fold, "val", subject, label, probability)]
        with tempfile.TemporaryDirectory() as directory:
            dump = write_dump(Path(directory) / "d.csv", rows)
            with self.assertRaises(SystemExit):
                wp.analyse_arm("arm", dump, 10, 1)

    def test_inconsistent_label_for_one_patient_is_rejected(self):
        rows, _, _ = build_rows(n_folds=3, n_per_fold=4, n_val_per_fold=2)
        fold, split, subject, label, probability = rows[0]
        other_split = "test" if split == "val" else "val"
        rows = rows + [(fold, other_split, subject, 1 - label, probability)]
        with tempfile.TemporaryDirectory() as directory:
            dump = write_dump(Path(directory) / "d.csv", rows)
            with self.assertRaises(SystemExit):
                wp.cohort_checks(wp.read_probability_dump(dump))

    def test_arm_report_has_every_required_section(self):
        rows, _, _ = build_rows(seed=19)
        with tempfile.TemporaryDirectory() as directory:
            report = wp.analyse_arm("arm", write_dump(Path(directory) / "d.csv", rows), 30, 5)
        self.assertEqual(report["arm"], "arm")
        self.assertEqual(report["workpoints"]["primary_representation"], "calibrated")
        for representation in wp.REPRESENTATIONS:
            for constraint in wp.CONSTRAINTS:
                entry = report["workpoints"][representation][constraint["id"]]
                self.assertEqual(len(entry["per_fold"]), 5)
                self.assertEqual(entry["pooled_oof"]["n_patients"], report["cohort"]["n_patients"])
                self.assertGreater(entry["pooled_oof"]["n_boot"], 0)
                for field in wp.METRIC_FIELDS:
                    self.assertIn(field, entry["pooled_oof"]["metrics"])
                    self.assertIn(field, entry["pooled_oof"]["ci95"])
                    self.assertIn(field, entry["summary"]["test"])
                self.assertIn("threshold", entry["summary"])
        pooled = report["calibration"]["pooled_oof"]
        for key in (
            "brier_raw",
            "brier_calibrated",
            "brier_delta",
            "slope_raw",
            "slope_calibrated",
            "intercept_raw",
            "intercept_calibrated",
            "ece_raw",
            "ece_calibrated",
        ):
            self.assertIn(key, pooled)
        summary = report["calibration"]["summary_mean_std_across_folds"]
        for key in ("brier_raw", "brier_calibrated", "slope_raw", "slope_calibrated"):
            self.assertEqual(len(summary[key]["values"]), 5)
        reliability = report["calibration"]["reliability_pooled_oof"]
        self.assertEqual(len(reliability["before_calibration"]), wp.RELIABILITY_BINS)
        self.assertEqual(len(reliability["after_calibration"]), wp.RELIABILITY_BINS)
        self.assertEqual(len(report["decision_curve"]["rows"]), len(wp.DCA_GRID))
        self.assertTrue(report["limitations"])


class EndToEndTests(unittest.TestCase):
    """``main`` writes the two deliverables and enforces the protocol guard."""

    def _reports(self, directory: Path, n_boot: int = 25):
        rows, _, _ = build_rows(seed=43)
        dump = write_dump(directory / "prob-a.csv", rows)
        other = [
            (fold, split, subject, label, min(1.0, probability * 0.9 + 0.05))
            for fold, split, subject, label, probability in rows
        ]
        dump_b = write_dump(directory / "prob-b.csv", other)
        cohort = wp.cohort_checks(wp.read_probability_dump(dump))
        expected = splits_fingerprint(cohort["assignment"])
        argv = [
            "--probs",
            f"a={dump}",
            f"b={dump_b}",
            "--out-dir",
            str(directory / "reports"),
            "--n-boot",
            str(n_boot),
            "--expected-fingerprint",
            expected,
        ]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            wp.main(argv)
        return directory / "reports", stdout.getvalue(), expected

    def test_main_writes_json_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            reports_dir, stdout, fingerprint = self._reports(Path(directory))
            self.assertIn(fingerprint[:16], stdout)
            for arm in ("a", "b"):
                path = reports_dir / f"workpoint-{arm}.json"
                self.assertTrue(path.exists())
                report = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(report["arm"], arm)
                self.assertEqual(report["cohort"]["splits_fingerprint"], fingerprint)
                self.assertEqual(report["protocol"]["threshold_selection_split"], "val")
                self.assertEqual(report["protocol"]["evaluation_split"], "test")
            summary = (reports_dir / "workpoint-summary.md").read_text(encoding="utf-8")
            for marker in (
                "## 1. Protocol and provenance",
                "## 2. Calibration",
                "## 3. Workpoints",
                "## 4. Decision-curve",
                "## 6. Deployment recommendation",
                "## 7. Which numbers are still NOT deployable",
                "## 8. Decision record",
                "W1 screening",
                "W2 high_sensitivity",
                "W3 balanced",
                "treat all",
                "treat none",
            ):
                self.assertIn(marker, summary)
            for arm in ("a", "b"):
                self.assertIn(f"`{arm}`", summary)

    def test_main_refuses_a_different_split(self):
        with tempfile.TemporaryDirectory() as directory:
            rows, _, _ = build_rows(seed=47)
            dump = write_dump(Path(directory) / "d.csv", rows)
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stdout(io.StringIO()):
                    wp.main(
                        [
                            "--probs",
                            f"a={dump}",
                            "--out-dir",
                            str(Path(directory) / "reports"),
                            "--n-boot",
                            "10",
                            "--expected-fingerprint",
                            "0" * 64,
                        ]
                    )
            self.assertFalse((Path(directory) / "reports" / "workpoint-a.json").exists())

    def test_main_allows_an_explicitly_different_split(self):
        with tempfile.TemporaryDirectory() as directory:
            rows, _, _ = build_rows(seed=53)
            dump = write_dump(Path(directory) / "d.csv", rows)
            with contextlib.redirect_stdout(io.StringIO()):
                wp.main(
                    [
                        "--probs",
                        f"a={dump}",
                        "--out-dir",
                        str(Path(directory) / "reports"),
                        "--n-boot",
                        "10",
                        "--expected-fingerprint",
                        "0" * 64,
                        "--allow-other-splits",
                    ]
                )
            self.assertTrue((Path(directory) / "reports" / "workpoint-a.json").exists())

    def test_recommendation_follows_the_stated_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            reports_dir, _, _ = self._reports(Path(directory))
            reports = {
                arm: json.loads((reports_dir / f"workpoint-{arm}.json").read_text())
                for arm in ("a", "b")
            }
        recommendation = wp.deployment_recommendation(reports)
        self.assertEqual(recommendation["constraint"], "W1")
        self.assertEqual(recommendation["specificity_floor"], wp.FLOOR_SPECIFICITY)
        # The arm is chosen on validation discrimination only: the test-fold
        # specificity is an evaluation of that choice, never an input to it.
        best = max(
            recommendation["ranking"], key=lambda row: row["val_auroc_mean"]
        )
        self.assertEqual(recommendation["selected_arm"], best["arm"])
        self.assertEqual(
            recommendation["selected"]["val_auroc_mean"],
            max(row["val_auroc_mean"] for row in recommendation["ranking"]),
        )
        # floor_met is computed from the pooled test specificity, not chosen.
        self.assertEqual(
            recommendation["floor_met"],
            best["specificity"] >= wp.FLOOR_SPECIFICITY,
        )
        self.assertEqual(
            recommendation["arms_meeting_floor_on_test"],
            [
                row["arm"]
                for row in recommendation["ranking"]
                if row["specificity"] >= wp.FLOOR_SPECIFICITY
            ],
        )

    def test_arm_choice_ignores_test_fold_performance(self):
        """The same arm must be chosen whether its test scores are real or inverted."""
        with tempfile.TemporaryDirectory() as directory:
            rows, _, _ = build_rows(seed=61)
            weak, _, _ = build_rows(seed=62, signal=0.3)
            normal = wp.analyse_arm(
                "candidate", write_dump(Path(directory) / "a.csv", rows), 20, 2
            )
            inverted_rows = [
                (
                    fold,
                    split,
                    subject,
                    label,
                    probability if split == "val" else 1.0 - probability,
                )
                for fold, split, subject, label, probability in rows
            ]
            inverted = wp.analyse_arm(
                "candidate", write_dump(Path(directory) / "b.csv", inverted_rows), 20, 2
            )
            control = wp.analyse_arm(
                "control", write_dump(Path(directory) / "c.csv", weak), 20, 2
            )
        self.assertNotEqual(
            normal["workpoints"]["calibrated"]["W1"]["pooled_oof"]["metrics"]["sensitivity"],
            inverted["workpoints"]["calibrated"]["W1"]["pooled_oof"]["metrics"]["sensitivity"],
            "the perturbation must actually change the reported test performance",
        )
        first = wp.deployment_recommendation({"candidate": normal, "control": control})
        second = wp.deployment_recommendation({"candidate": inverted, "control": control})
        self.assertEqual(first["selected_arm"], second["selected_arm"])
        self.assertEqual(first["selected_arm"], "candidate")
        self.assertLess(
            control["arm_selection"]["val_auroc"]["mean"],
            normal["arm_selection"]["val_auroc"]["mean"],
        )
        # ... and the floor evaluation does follow the test rows, so this is not
        # a test that simply ignores the test fold.
        self.assertNotEqual(first["floor_met"] and first["selected"]["specificity"],
                            second["floor_met"] and second["selected"]["specificity"])


class TrainingScriptDumpTests(unittest.TestCase):
    """The flag must not change anything else, proven on the real entry point."""

    def test_report_json_is_byte_identical_without_the_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            dataset = directory / "synthetic"
            subprocess.run(
                [
                    PYTHON,
                    str(AI_DIR / "synthetic_pcg.py"),
                    "--out-dir",
                    str(dataset),
                    "--subjects",
                    "8",
                ],
                check=True,
                capture_output=True,
            )
            common = [
                PYTHON,
                str(AI_DIR / "cross_validate.py"),
                "--data-dir",
                str(dataset),
                "--model",
                "logreg",
                # 8 synthetic patients over 5 folds would leave a fold empty.
                "--folds",
                "4",
            ]
            plain = subprocess.run(
                common + ["--out-dir", str(directory / "plain")],
                check=True,
                capture_output=True,
                text=True,
            )
            flagged = subprocess.run(
                common
                + [
                    "--out-dir",
                    str(directory / "flagged"),
                    "--dump-probabilities",
                    str(directory / "dump.csv"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                (directory / "plain" / "report.json").read_bytes(),
                (directory / "flagged" / "report.json").read_bytes(),
                "the dump flag must not change report.json",
            )
            self.assertEqual(
                (directory / "plain" / "report.md").read_bytes(),
                (directory / "flagged" / "report.md").read_bytes(),
            )
            self.assertNotIn("probability rows", plain.stdout)
            self.assertIn("probability rows", flagged.stdout)

            dump = directory / "dump.csv"
            data = wp.read_probability_dump(dump)
            self.assertEqual(len(set(data["split"])), 2)
            self.assertEqual(set(data["split"]), {"val", "test"})
            # The dump must reproduce the report's patient count per fold, i.e. the
            # aggregation is the same one the CV metrics used.
            report = json.loads((directory / "flagged" / "report.json").read_text())
            for fold in report["per_fold"]:
                index = report["per_fold"].index(fold)
                test_rows = np.sum((data["fold"] == index) & (data["split"] == "test"))
                self.assertEqual(int(test_rows), fold["n_test_subjects"])
                val_rows = np.sum((data["fold"] == index) & (data["split"] == "val"))
                self.assertEqual(int(val_rows), fold["n_val_subjects"])


if __name__ == "__main__":
    unittest.main()
