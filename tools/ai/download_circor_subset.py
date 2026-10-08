#!/usr/bin/env python3
"""Download a small, balanced subset of CirCor 1.0.3 recordings.

The full public archive is ~471 MB. On restricted networks it is more useful
to first fetch a handful of subjects and run the pipeline end to end. This
script keeps the downloaded subjects in a separate directory so the full
archive can still be extracted later without collisions.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE_URL = "https://physionet.org/files/circor-heart-sound/1.0.3/training_data/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metadata",
        default="data/raw/circor/training_data.csv",
        help="Subject metadata CSV.",
    )
    parser.add_argument(
        "--out-dir",
        default="data/raw/circor/subset",
        help="Subset root; WAV files go to <out-dir>/training_data/.",
    )
    parser.add_argument("--subjects-per-class", type=int, default=12)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--seed", type=int, default=20261007)
    parser.add_argument(
        "--include-annotations",
        action="store_true",
        help="Also fetch the per-recording .tsv segmentation files.",
    )
    return parser.parse_args()


def load_subjects(metadata_path: Path) -> dict[str, dict]:
    subjects: dict[str, dict] = {}
    with metadata_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            subject_id = (row.get("Patient ID") or "").strip()
            murmur = (row.get("Murmur") or "").strip()
            if subject_id and murmur in ("Present", "Absent"):
                subjects[subject_id] = {"subject_id": subject_id, "murmur": murmur}
    return subjects


def select_subjects(
    subjects: dict[str, dict], per_class: int, seed: int
) -> list[dict]:
    rng = random.Random(seed)
    selected: list[dict] = []
    for murmur in ("Present", "Absent"):
        rows = [row for row in subjects.values() if row["murmur"] == murmur]
        rows.sort(key=lambda row: row["subject_id"])
        rng.shuffle(rows)
        selected.extend(rows[:per_class])
    return sorted(selected, key=lambda row: row["subject_id"])


def records_for_subject(records_path: Path, subject_id: str) -> list[str]:
    records = []
    with records_path.open(encoding="utf-8") as handle:
        for line in handle:
            name = line.strip()
            if not name:
                continue
            record = name.rsplit("/", 1)[-1]
            if record.split("_")[0] == subject_id:
                records.append(record)
    return records


def fetch(url: str, destination: Path, retries: int, timeout: float) -> tuple[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return str(destination), destination.stat().st_size
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "FYP-heart-sound-research/0.1"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
            destination.write_bytes(payload)
            return str(destination), len(payload)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"{url}: {last_error}")


def main() -> None:
    args = parse_args()
    metadata_path = Path(args.metadata)
    out_dir = Path(args.out_dir)
    training_dir = out_dir / "training_data"
    records_path = Path("data/raw/circor/RECORDS")

    subjects = load_subjects(metadata_path)
    selected = select_subjects(subjects, args.subjects_per_class, args.seed)
    print(
        "[subset] selected "
        + ", ".join(f"{row['subject_id']}:{row['murmur']}" for row in selected)
    )

    jobs: list[tuple[str, Path]] = []
    for row in selected:
        for record in records_for_subject(records_path, row["subject_id"]):
            jobs.append((BASE_URL + record + ".wav", training_dir / f"{record}.wav"))
            if args.include_annotations:
                jobs.append((BASE_URL + record + ".tsv", training_dir / f"{record}.tsv"))

    print(f"[subset] {len(jobs)} files queued, concurrency={args.concurrency}")
    started = time.time()
    downloaded: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(fetch, url, path, args.retries, args.timeout): (url, path)
            for url, path in jobs
        }
        for index, future in enumerate(as_completed(futures), start=1):
            url, path = futures[future]
            try:
                _, size = future.result()
                downloaded.append({"path": str(path), "bytes": size})
            except Exception as error:  # noqa: BLE001 - report every failed URL
                errors.append(f"{url}: {error}")
                print(f"[subset] FAILED {url}: {error}")
            if index % 10 == 0 or index == len(jobs):
                elapsed = time.time() - started
                print(
                    f"[subset] {index}/{len(jobs)} files, "
                    f"{sum(item['bytes'] for item in downloaded) / 1e6:.1f} MB, {elapsed:.0f}s"
                )

    metadata_copy = out_dir / "training_data.csv"
    metadata_copy.write_bytes(metadata_path.read_bytes())
    (out_dir / ".extracted").write_text("subset\n", encoding="utf-8")
    manifest = {
        "source": "https://physionet.org/content/circor-heart-sound/1.0.3/",
        "license": "ODC-By 1.0",
        "subjects": selected,
        "files": downloaded,
        "errors": errors,
        "elapsed_s": round(time.time() - started, 1),
    }
    (out_dir / "subset_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"[subset] wrote {out_dir / 'subset_manifest.json'}")
    if errors:
        print(f"[subset] {len(errors)} files failed; rerun to retry them")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
