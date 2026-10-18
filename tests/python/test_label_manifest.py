"""Tests for deriving alternative-label manifests and the INT8 fold driver."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools" / "ai"
sys.path.insert(0, str(TOOLS))

from hs_splits import load_splits, splits_fingerprint


def write_manifest(directory: Path, rows: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    subject_fields = ["subject_id", "murmur", "label", "outcome", "campaign", "n_recordings"]
    recording_fields = ["subject_id", "record_id", "label", "wav_path"]
    subjects = []
    recordings = []
    for index, row in enumerate(rows):
        subjects.append(
            {
                "subject_id": row["subject_id"],
                "murmur": row["murmur"],
                "label": row["label"],
                "outcome": row["outcome"],
                "campaign": row["campaign"],
                "n_recordings": 1,
            }
        )
        recordings.append(
            {
                "subject_id": row["subject_id"],
                "record_id": f"{row['subject_id']}_AV",
                "label": row["label"],
                "wav_path": f"/nonexistent/{row['subject_id']}_AV.wav",
            }
        )
    for path, rows_, fields in (
        (directory / "subjects.csv", subjects, subject_fields),
        (directory / "recordings.csv", recordings, recording_fields),
    ):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows_)


def run_manifest(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOLS / "make_label_manifest.py"), *args],
        cwd=ROOT, capture_output=True, text=True,
    )


class ManifestDerivationTests(unittest.TestCase):
    def make_rows(self):
        rows = []
        for index in range(12):
            rows.append(
                {
                    "subject_id": f"s{index:02d}",
                    "murmur": "Present" if index % 3 == 0 else "Absent",
                    "label": "1" if index % 3 == 0 else "0",
                    "outcome": "Abnormal" if index % 2 == 0 else "Normal",
                    "campaign": "CC2014" if index < 5 else "CC2015",
                }
            )
        rows.append({"subject_id": "u00", "murmur": "Unknown", "label": "", "outcome": "Normal", "campaign": "CC2015"})
        return rows

    def test_outcome_relabels_and_keeps_unknown_murmur_patients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            out = Path(tmp) / "out"
            write_manifest(source, self.make_rows())
            completed = run_manifest("--source", str(source), "--out", str(out), "--label", "outcome")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads((out / "summary.json").read_text())
            # All 13 patients have an outcome, including the Unknown-murmur one.
            self.assertEqual(summary["subjects"], 13)
            self.assertEqual(summary["label_counts"], {"0": 7, "1": 6})
            # Labels in recordings.csv must match the new target.
            with (out / "recordings.csv").open(newline="") as handle:
                for row in csv.DictReader(handle):
                    expected = "1" if int(row["subject_id"][1:]) % 2 == 0 else "0"
                    if row["subject_id"] == "u00":
                        expected = "0"  # outcome Normal
                    self.assertEqual(row["label"], expected, row["subject_id"])

    def test_murmur_drops_unknown_patients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            out = Path(tmp) / "out"
            write_manifest(source, self.make_rows())
            completed = run_manifest("--source", str(source), "--out", str(out), "--label", "murmur")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["subjects"], 12)
            self.assertNotIn("u00", load_splits(out / "splits.csv"))

    def test_campaign_split_uses_two_folds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            out = Path(tmp) / "out"
            write_manifest(source, self.make_rows())
            completed = run_manifest(
                "--source", str(source), "--out", str(out), "--label", "outcome", "--splits", "campaign"
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            folds = load_splits(out / "splits.csv")
            self.assertEqual(sorted(set(folds.values())), [0, 1])
            # s00..s04 are CC2014.
            for subject in ("s00", "s01", "s02", "s03", "s04"):
                self.assertEqual(folds[subject], 0)
            self.assertEqual(folds["s05"], 1)

    def test_frozen_split_fingerprint_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            out = Path(tmp) / "out"
            write_manifest(source, self.make_rows())
            completed = run_manifest("--source", str(source), "--out", str(out), "--label", "outcome")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["fingerprint"], splits_fingerprint(load_splits(out / "splits.csv")))

    def test_labelled_subject_without_recording_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            out = Path(tmp) / "out"
            write_manifest(source, self.make_rows())
            # Drop every recording of s00 from recordings.csv only.
            path = source / "recordings.csv"
            lines = [line for line in path.read_text().splitlines() if "s00_AV" not in line]
            path.write_text("\n".join(lines) + "\n")
            completed = run_manifest("--source", str(source), "--out", str(out), "--label", "outcome")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("no recording", completed.stderr)


class Int8DriverTests(unittest.TestCase):
    def test_mean_std_handles_none_and_single(self) -> None:
        from int8_cv import mean_std

        self.assertEqual(mean_std([None, None]), {"mean": None, "std": None, "n": 0})
        self.assertEqual(mean_std([0.5])["std"], 0.0)
        stats = mean_std([0.80, 0.84])
        self.assertAlmostEqual(stats["mean"], 0.82)
        self.assertAlmostEqual(stats["std"], 0.028284271247461902)


if __name__ == "__main__":
    unittest.main()
