"""Unit tests for frozen patient-level split files (``hs_splits``).

The frozen split is the protocol artifact that makes results from different
scripts and dates comparable, so these tests pin the three properties it has to
guarantee: deterministic patient-level stratification, a complete and
non-overlapping fold assignment, and a fingerprint that changes exactly when the
assignment changes.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "ai"))

import cross_validate
from hs_splits import (
    DEFAULT_N_FOLDS,
    DEFAULT_SEED,
    fold_label_counts,
    fold_sets,
    load_splits,
    patient_folds,
    seed_protocol,
    splits_fingerprint,
    validate_splits,
    write_splits,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AI_DIR = REPO_ROOT / "tools" / "ai"
PYTHON = sys.executable
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def make_labels(n_negative: int = 20, n_positive: int = 17) -> dict[str, int]:
    """Two-class synthetic cohort with stable subject ids."""
    labels = {f"N{index:03d}": 0 for index in range(n_negative)}
    labels.update({f"P{index:03d}": 1 for index in range(n_positive)})
    return labels


def run_cli(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, str(script), *args], capture_output=True, text=True
    )


class PatientFoldTests(unittest.TestCase):
    def test_same_inputs_give_identical_assignment(self) -> None:
        labels = make_labels()
        first = patient_folds(labels, 5, DEFAULT_SEED)
        # Insertion order must not matter: only the sorted cohort and seed do.
        reordered = dict(reversed(list(labels.items())))
        second = patient_folds(reordered, 5, DEFAULT_SEED)
        self.assertEqual(first, second)

    def test_different_seed_changes_assignment(self) -> None:
        labels = make_labels()
        baseline = patient_folds(labels, 5, DEFAULT_SEED)
        other = patient_folds(labels, 5, DEFAULT_SEED + 1)
        self.assertNotEqual(baseline, other)
        # ... while remaining a valid partition of the same cohort.
        self.assertEqual(sorted(baseline), sorted(other))

    def test_every_subject_in_exactly_one_fold(self) -> None:
        labels = make_labels()
        folds = patient_folds(labels, 5, DEFAULT_SEED)
        groups = fold_sets(folds, 5)
        self.assertEqual(len(groups), 5)
        self.assertEqual(set(folds), set(labels))
        union: set[str] = set()
        for group in groups:
            self.assertFalse(union & group, "a subject appears in two folds")
            union |= group
        self.assertEqual(union, set(labels))
        self.assertEqual(sum(len(group) for group in groups), len(labels))

    def test_per_class_counts_within_one_of_ideal(self) -> None:
        cases = [
            (20, 17, 5),
            (7, 6, 5),
            (3, 1, 5),
            (40, 40, 5),
            (9, 8, 3),
            (1, 30, 5),
        ]
        for n_negative, n_positive, n_folds in cases:
            with self.subTest(negative=n_negative, positive=n_positive, folds=n_folds):
                labels = make_labels(n_negative, n_positive)
                counts = fold_label_counts(
                    patient_folds(labels, n_folds, DEFAULT_SEED), labels, n_folds
                )
                for label, total in ((0, n_negative), (1, n_positive)):
                    ideal = total / n_folds
                    for fold in range(n_folds):
                        observed = counts[fold].get(label, 0)
                        self.assertLessEqual(
                            abs(observed - ideal),
                            1.0,
                            f"label {label} fold {fold}: {observed} vs ideal {ideal}",
                        )

    def test_reproduces_historical_seed_derived_folds(self) -> None:
        """A frozen file with --seed 20261008 must re-create ADR-007's folds."""
        labels = make_labels(37, 29)
        for seed in (20261008, 1, 1234567):
            with self.subTest(seed=seed):
                historical = cross_validate.stratified_folds(labels, 5, seed)
                frozen = fold_sets(patient_folds(labels, 5, seed), 5)
                self.assertEqual(frozen, historical)

    def test_rejects_empty_cohort_and_impossible_fold_count(self) -> None:
        with self.assertRaises(ValueError):
            patient_folds({}, 5, DEFAULT_SEED)
        with self.assertRaises(ValueError):
            patient_folds(make_labels(), 1, DEFAULT_SEED)


class FingerprintTests(unittest.TestCase):
    def test_stable_across_calls_and_input_order(self) -> None:
        labels = make_labels()
        folds = patient_folds(labels, 5, DEFAULT_SEED)
        self.assertEqual(splits_fingerprint(folds), splits_fingerprint(dict(folds)))
        self.assertEqual(
            splits_fingerprint(folds),
            splits_fingerprint(dict(reversed(list(folds.items())))),
        )
        self.assertRegex(splits_fingerprint(folds), SHA256_HEX)

    def test_changes_when_one_subject_moves(self) -> None:
        labels = make_labels()
        folds = patient_folds(labels, 5, DEFAULT_SEED)
        moved = dict(folds)
        subject = sorted(moved)[0]
        moved[subject] = (moved[subject] + 1) % 5
        self.assertNotEqual(splits_fingerprint(folds), splits_fingerprint(moved))

    def test_survives_write_and_load(self) -> None:
        labels = make_labels()
        folds = patient_folds(labels, 5, DEFAULT_SEED)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "splits.csv"
            write_splits(path, folds, labels, {"dataset": "unit-test", "seed": 7})
            self.assertEqual(splits_fingerprint(load_splits(path)), splits_fingerprint(folds))


class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.labels = make_labels(7, 6)
        self.folds = patient_folds(self.labels, 5, DEFAULT_SEED)

    def test_accepts_valid_assignment(self) -> None:
        self.assertIsNone(validate_splits(self.folds, self.labels, 5))

    def test_rejects_unknown_fold_index(self) -> None:
        broken = dict(self.folds)
        broken[sorted(broken)[0]] = 5
        with self.assertRaises(ValueError):
            validate_splits(broken, self.labels, 5)

    def test_rejects_negative_and_non_integer_fold(self) -> None:
        broken = dict(self.folds)
        broken[sorted(broken)[0]] = -1
        with self.assertRaises(ValueError):
            validate_splits(broken, self.labels, 5)
        broken[sorted(broken)[0]] = "fold-a"
        with self.assertRaises(ValueError):
            validate_splits(broken, self.labels, 5)

    def test_rejects_missing_subject(self) -> None:
        incomplete = dict(self.folds)
        del incomplete[sorted(incomplete)[0]]
        with self.assertRaises(ValueError):
            validate_splits(incomplete, self.labels, 5)

    def test_rejects_duplicate_subject_across_folds(self) -> None:
        duplicate = sorted(self.labels)[0]
        pairs = list(self.folds.items()) + [(duplicate, (self.folds[duplicate] + 1) % 5)]
        with self.assertRaises(ValueError):
            validate_splits(pairs, self.labels, 5)

    def test_rejects_duplicate_subject_in_same_fold(self) -> None:
        duplicate = sorted(self.labels)[0]
        pairs = list(self.folds.items()) + [(duplicate, self.folds[duplicate])]
        with self.assertRaises(ValueError):
            validate_splits(pairs, self.labels, 5)

    def test_rejects_unknown_subject_when_strict(self) -> None:
        extra = dict(self.folds)
        extra["UNSEEN"] = 0
        with self.assertRaises(ValueError):
            validate_splits(extra, self.labels, 5, strict_extra=True)

    def test_tolerates_foreign_subject_by_default(self) -> None:
        # A frozen file may cover patients whose windows a filtered run drops;
        # those patients contribute nothing and must not break the arm.
        extra = dict(self.folds)
        extra["UNSEEN"] = 0
        self.assertIsNone(validate_splits(extra, self.labels, 5))


class RoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.labels = make_labels(7, 6)
        self.folds = patient_folds(self.labels, 5, DEFAULT_SEED)

    def test_write_then_load_returns_same_mapping(self) -> None:
        path = write_splits(
            self.root / "nested" / "splits.csv",
            self.folds,
            self.labels,
            {"dataset": "unit-test", "n_folds": 5, "seed": DEFAULT_SEED},
        )
        self.assertTrue(path.exists())
        self.assertEqual(load_splits(path), self.folds)

    def test_file_shape_is_stable(self) -> None:
        path = write_splits(self.root / "splits.csv", self.folds, self.labels, {"seed": 1})
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("# "))
        self.assertIn("subject_id,fold,label", lines)
        header_index = lines.index("subject_id,fold,label")
        self.assertEqual(len(lines) - header_index - 1, len(self.labels))
        # One row per patient, sorted by subject id, with its manifest label.
        rows = [line.split(",") for line in lines[header_index + 1 :]]
        self.assertEqual([row[0] for row in rows], sorted(self.labels))
        for subject, fold, label in rows:
            self.assertEqual(int(fold), self.folds[subject])
            self.assertEqual(int(label), self.labels[subject])

    def test_metadata_is_ignored_by_loader(self) -> None:
        plain = write_splits(self.root / "plain.csv", self.folds, self.labels, None)
        laden = write_splits(
            self.root / "laden.csv", self.folds, self.labels, {"note": "a: b, # c"}
        )
        self.assertEqual(load_splits(plain), load_splits(laden))

    def test_write_rejects_incomplete_or_gapped_assignment(self) -> None:
        missing = dict(self.folds)
        del missing[sorted(missing)[0]]
        with self.assertRaises(ValueError):
            write_splits(self.root / "bad.csv", missing, self.labels, None)
        gapped = dict(self.folds)
        subject = sorted(gapped)[0]
        gapped[subject] = 7
        with self.assertRaises(ValueError):
            write_splits(self.root / "gapped.csv", gapped, self.labels, None)
        with self.assertRaises(ValueError):
            write_splits(self.root / "multi.csv", self.folds, self.labels, {"x": "a\nb"})

    def test_load_rejects_malformed_files(self) -> None:
        path = self.root / "dup.csv"
        path.write_text(
            "subject_id,fold,label\nN000,0,0\nN000,1,0\n", encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            load_splits(path)
        path = self.root / "header.csv"
        path.write_text("subject,fold,label\nN000,0,0\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_splits(path)
        path = self.root / "empty.csv"
        path.write_text("# only metadata\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_splits(path)


class FreezeSplitsCliTests(unittest.TestCase):
    """End-to-end: synthetic manifest -> freeze_splits.py -> summary.json."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.data_dir = cls.root / "synthetic"
        generated = run_cli(
            AI_DIR / "synthetic_pcg.py",
            "--out-dir",
            str(cls.data_dir),
            "--subjects",
            "12",
        )
        if generated.returncode != 0:
            raise RuntimeError(f"synthetic_pcg.py failed:\n{generated.stderr}")
        cls.frozen = run_cli(AI_DIR / "freeze_splits.py", "--data-dir", str(cls.data_dir))
        if cls.frozen.returncode != 0:
            raise RuntimeError(f"freeze_splits.py failed:\n{cls.frozen.stderr}")
        cls.splits_path = cls.data_dir / "splits.csv"
        cls.summary_path = cls.data_dir / "splits-summary.json"
        cls.summary = json.loads(cls.summary_path.read_text(encoding="utf-8"))
        cls.folds = load_splits(cls.splits_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_writes_csv_and_summary(self) -> None:
        self.assertTrue(self.splits_path.exists())
        self.assertTrue(self.summary_path.exists())
        self.assertIn("subject_id,fold,label", self.splits_path.read_text(encoding="utf-8"))
        self.assertEqual(self.summary["n_folds"], DEFAULT_N_FOLDS)
        self.assertEqual(self.summary["seed"], DEFAULT_SEED)
        self.assertEqual(DEFAULT_SEED, 20261008)
        self.assertEqual(self.summary["seed_protocol"], "2026-10-08 / ADR-007")
        text = self.splits_path.read_text(encoding="utf-8")
        self.assertIn("# seed: 20261008", text)
        self.assertIn("# seed_protocol: 2026-10-08 / ADR-007", text)
        self.assertEqual(self.summary["total_patients"], 12)
        self.assertRegex(self.summary["fingerprint"], SHA256_HEX)
        self.assertEqual(self.summary["fingerprint"], splits_fingerprint(self.folds))
        self.assertEqual(self.summary["source_manifest"], str(self.data_dir / "subjects.csv"))

    def test_split_file_covers_exactly_the_labelled_cohort(self) -> None:
        subjects = [
            row for row in _read_csv(self.data_dir / "subjects.csv")
            if row["label"] in ("0", "1")
        ]
        self.assertEqual(sorted(self.folds), sorted(row["subject_id"] for row in subjects))
        self.assertEqual(self.summary["label_counts"], _count_labels(subjects))

    def test_summary_counts_match_the_split_file(self) -> None:
        rows = _read_csv(self.splits_path)
        self.assertEqual(len(rows), self.summary["total_patients"])
        self.assertEqual(len(set(row["subject_id"] for row in rows)), len(rows))
        per_fold: dict[str, int] = {}
        per_fold_label: dict[str, dict[str, int]] = {}
        for row in rows:
            fold = row["fold"]
            per_fold[fold] = per_fold.get(fold, 0) + 1
            bucket = per_fold_label.setdefault(fold, {})
            bucket[row["label"]] = bucket.get(row["label"], 0) + 1
        expected_folds = {str(fold): per_fold.get(str(fold), 0) for fold in range(DEFAULT_N_FOLDS)}
        self.assertEqual(self.summary["fold_patient_counts"], expected_folds)
        self.assertEqual(
            {
                fold: {
                    label: per_fold_label.get(fold, {}).get(label, 0)
                    for label in sorted(self.summary["label_counts"])
                }
                for fold in expected_folds
            },
            self.summary["fold_label_counts"],
        )
        self.assertEqual(sum(per_fold.values()), self.summary["total_patients"])
        for label, total in self.summary["label_counts"].items():
            self.assertEqual(
                sum(int(self.summary["fold_label_counts"][fold][label]) for fold in expected_folds),
                total,
            )

    def test_frozen_folds_are_stratified(self) -> None:
        for label, total in self.summary["label_counts"].items():
            ideal = total / DEFAULT_N_FOLDS
            for fold, counts in self.summary["fold_label_counts"].items():
                with self.subTest(label=label, fold=fold):
                    self.assertLessEqual(abs(counts[label] - ideal), 1.0)
        self.assertLessEqual(self.summary["per_class_max_abs_deviation_from_ideal"], 1.0)

    def test_freeze_defaults_reproduce_historical_adr007_folds(self) -> None:
        """The frozen file must equal cross_validate.stratified_folds, not just claim it."""
        self.assertEqual(
            DEFAULT_SEED,
            20261008,
            "DEFAULT_SEED must stay the ADR-007 protocol seed (2026-10-08)",
        )
        out_path = self.root / "adr007" / "splits.csv"
        result = run_cli(
            AI_DIR / "freeze_splits.py",
            "--data-dir",
            str(self.data_dir),
            "--seed",
            "20261008",
            "--out",
            str(out_path),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        adr_summary = json.loads(
            (out_path.parent / "splits-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(adr_summary["seed_protocol"], "2026-10-08 / ADR-007")
        self.assertIn("seed=20261008", result.stdout)
        self.assertIn(adr_summary["fingerprint"], result.stdout)

        cohort = {
            row["subject_id"]: int(row["label"])
            for row in _read_csv(self.data_dir / "subjects.csv")
            if row["label"] in ("0", "1")
        }
        historical = cross_validate.stratified_folds(cohort, DEFAULT_N_FOLDS, 20261008)
        expected = {
            subject: fold for fold, group in enumerate(historical) for subject in group
        }
        frozen = load_splits(out_path)
        if frozen != expected:
            differences = []
            for fold in range(DEFAULT_N_FOLDS):
                got = {subject for subject, value in frozen.items() if value == fold}
                want = historical[fold]
                if got != want:
                    differences.append(
                        f"fold {fold}: frozen-only={sorted(got - want)}, "
                        f"historical-only={sorted(want - got)}"
                    )
            self.fail(
                "frozen splits diverge from cross_validate.stratified_folds"
                f"(seed=20261008): {' | '.join(differences)}"
            )
        # A file frozen without --seed must be the published protocol too.
        self.assertEqual(self.folds, frozen)

    def test_rerun_is_byte_identical_in_fingerprint(self) -> None:
        second = run_cli(
            AI_DIR / "freeze_splits.py",
            "--data-dir",
            str(self.data_dir),
            "--out",
            str(self.root / "again" / "splits.csv"),
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        again = load_splits(self.root / "again" / "splits.csv")
        self.assertEqual(again, self.folds)
        self.assertEqual(
            splits_fingerprint(again), self.summary["fingerprint"]
        )
        again_summary = json.loads(
            (self.root / "again" / "splits-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(again_summary["fingerprint"], self.summary["fingerprint"])

    def test_refuses_inconsistent_labels(self) -> None:
        broken_dir = self.root / "broken"
        shutil.copytree(self.data_dir, broken_dir)
        recordings_path = broken_dir / "recordings.csv"
        rows = _read_csv(recordings_path)
        victim = next(row for row in rows if row["label"] == "0")
        victim["label"] = "1"
        _write_csv(recordings_path, rows)
        out_path = self.root / "broken-out" / "splits.csv"
        result = run_cli(
            AI_DIR / "freeze_splits.py", "--data-dir", str(broken_dir), "--out", str(out_path)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inconsistent", result.stdout + result.stderr)
        self.assertFalse(out_path.exists())

    def test_refuses_too_many_folds(self) -> None:
        result = run_cli(
            AI_DIR / "freeze_splits.py",
            "--data-dir",
            str(self.data_dir),
            "--n-folds",
            "13",
            "--out",
            str(self.root / "too-many" / "splits.csv"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fold", (result.stdout + result.stderr).lower())
        self.assertFalse((self.root / "too-many" / "splits.csv").exists())


class ProtocolSeedTests(unittest.TestCase):
    """The seed is protocol state, not a date: every arm must agree on it."""

    def test_default_seed_is_the_adr007_protocol(self) -> None:
        self.assertEqual(DEFAULT_SEED, 20261008)
        self.assertEqual(seed_protocol(), "2026-10-08 / ADR-007")
        self.assertIn("not ADR-007", seed_protocol(20261018))

    def test_other_arms_default_to_the_same_seed(self) -> None:
        import train_mfcc  # the MFCC arm reads the same frozen file

        original = sys.argv
        try:
            sys.argv = ["cross_validate.py"]
            cross_args = cross_validate.parse_args()
            sys.argv = ["train_mfcc.py"]
            mfcc_args = train_mfcc.parse_args()
        finally:
            sys.argv = original
        self.assertEqual(cross_args.seed, DEFAULT_SEED)
        self.assertEqual(mfcc_args.seed, DEFAULT_SEED)


class CrossValidationIntegrationTests(unittest.TestCase):
    """The ``--splits-file`` code path of cross_validate.py, without the CNN."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.labels = make_labels(7, 6)
        self.folds = patient_folds(self.labels, 5, DEFAULT_SEED)
        self.splits_path = write_splits(
            self.root / "splits.csv", self.folds, self.labels, {"dataset": "unit-test"}
        )

    def args(self, **overrides) -> argparse.Namespace:
        values = {
            "splits_file": str(self.splits_path),
            "folds": 5,
            "seed": 20261008,
            "val_ratio": 0.15,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_parse_args_exposes_splits_file(self) -> None:
        original = sys.argv
        try:
            sys.argv = ["cross_validate.py", "--splits-file", str(self.splits_path)]
            parsed = cross_validate.parse_args()
            sys.argv = ["cross_validate.py"]
            default = cross_validate.parse_args()
        finally:
            sys.argv = original
        self.assertEqual(Path(parsed.splits_file), self.splits_path)
        self.assertEqual(parsed.folds, 5)
        self.assertIsNone(default.splits_file)

    def test_default_path_still_derives_folds_from_seed(self) -> None:
        folds, n_folds, provenance = cross_validate.resolve_folds(
            self.args(splits_file=None), self.labels
        )
        self.assertIsNone(provenance)
        self.assertEqual(n_folds, 5)
        self.assertEqual(folds, cross_validate.stratified_folds(self.labels, 5, 20261008))

    def test_frozen_file_is_used_instead_of_seed(self) -> None:
        folds, n_folds, provenance = cross_validate.resolve_folds(self.args(), self.labels)
        self.assertEqual(n_folds, 5)
        self.assertEqual(folds, fold_sets(self.folds, 5))
        # The fingerprint in the report identifies the exact file used.
        self.assertEqual(provenance["fingerprint"], splits_fingerprint(self.folds))
        self.assertEqual(provenance["path"], str(self.splits_path))
        self.assertEqual(provenance["kind"], "frozen_splits_file")
        self.assertEqual(provenance["val_split"]["seed"], 20261008)

    def test_fingerprint_differs_for_a_different_frozen_file(self) -> None:
        other_path = write_splits(
            self.root / "other.csv",
            patient_folds(self.labels, 5, DEFAULT_SEED + 1),
            self.labels,
            None,
        )
        _, _, first = cross_validate.resolve_folds(self.args(), self.labels)
        _, _, second = cross_validate.resolve_folds(
            self.args(splits_file=str(other_path)), self.labels
        )
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_rejects_fold_count_mismatch(self) -> None:
        with self.assertRaises(SystemExit):
            cross_validate.resolve_folds(self.args(folds=3), self.labels)

    def test_rejects_subject_absent_from_frozen_file(self) -> None:
        labels = dict(self.labels)
        labels["UNSEEN"] = 1
        with self.assertRaises(SystemExit):
            cross_validate.resolve_folds(self.args(), labels)

    def test_ignores_frozen_subjects_without_windows(self) -> None:
        labels = dict(self.labels)
        # Drop a subject from a fold that keeps at least one other patient, so
        # this exercises the warning path rather than an empty fold.
        fold_sizes: dict[int, int] = {}
        for subject in labels:
            fold_sizes[self.folds[subject]] = fold_sizes.get(self.folds[subject], 0) + 1
        dropped = next(
            subject for subject in sorted(labels)
            if fold_sizes[self.folds[subject]] > 1
        )
        del labels[dropped]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            folds, n_folds, provenance = cross_validate.resolve_folds(self.args(), labels)
        self.assertIn("warning", buffer.getvalue())
        self.assertEqual(n_folds, 5)
        self.assertNotIn(dropped, set().union(*folds))
        self.assertEqual(provenance["fingerprint"], splits_fingerprint({k: self.folds[k] for k in labels}))


def _read_csv(path: Path) -> list[dict]:
    """Read a manifest or a splits.csv (``#`` provenance lines are skipped)."""
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return list(csv.DictReader(lines))


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _count_labels(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["label"]] = counts.get(row["label"], 0) + 1
    return counts


if __name__ == "__main__":
    unittest.main()
