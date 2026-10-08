#!/usr/bin/env python3
"""Verify the downloaded CirCor 1.0.3 archive against the official checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--zip", default="data/raw/circor/circor-heart-sound-1.0.3.zip"
    )
    parser.add_argument(
        "--sums", default="data/raw/circor/SHA256SUMS.txt"
    )
    return parser.parse_args()


def sha256_of(handle, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = handle.read(chunk_size)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    zip_path = Path(args.zip)
    sums_path = Path(args.sums)
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            expected[parts[1].strip()] = parts[0].strip()

    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        prefix = ""
        if names and "/" in names[0]:
            prefix = names[0][: names[0].index("/") + 1]
        actual_name = {}
        for name in names:
            normalized = name[len(prefix) :] if prefix and name.startswith(prefix) else name
            actual_name[normalized] = name
        bad_crc = archive.testzip()
        mismatches: list[str] = []
        missing: list[str] = []
        for name, digest in expected.items():
            target = actual_name.get(name)
            if target is None:
                missing.append(name)
                continue
            with archive.open(target) as handle:
                actual = sha256_of(handle)
            if actual != digest:
                mismatches.append(name)
        extras = sorted(set(actual_name) - set(expected))

    suffix_counts = Counter(Path(name).suffix.lower() for name in names)
    report = {
        "zip": str(zip_path),
        "zip_bytes": zip_path.stat().st_size,
        "entries": len(names),
        "expected_entries": len(expected),
        "crc_error_entry": bad_crc,
        "checksum_mismatch_count": len(mismatches),
        "checksum_mismatches": mismatches[:20],
        "missing_entry_count": len(missing),
        "missing_entries": missing[:20],
        "extra_entry_count": len(extras),
        "extra_entries": extras[:20],
        "suffix_counts": dict(sorted(suffix_counts.items())),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    ok = bad_crc is None and not mismatches and not missing
    print("VERIFIED" if ok else "VERIFICATION FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
