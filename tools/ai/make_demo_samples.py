#!/usr/bin/env python3
"""Copy a few labelled Yaseen clips into pwa/samples/ so the demo can be tried
without transferring a file to the phone first.

Selection rule, fixed in advance and independent of the model: **the first two
recordings of each class, sorted by file name**. Nothing here looks at a
prediction, so the sample cannot be cherry-picked to flatter the model -- if it
gets one wrong, the page shows it wrong and that is the honest demo.

    .venv/bin/python tools/ai/make_demo_samples.py
    .venv/bin/python tools/ai/make_demo_samples.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import wave
from pathlib import Path

CLASSES = ("AS", "MS", "MR", "MVP", "N")
PER_CLASS = 2
RAW = Path("data/raw/yaseen/extracted")
OUT = Path("pwa/samples")


def pick(raw: Path) -> list[dict]:
    items = []
    for label in CLASSES:
        files = sorted((raw / label).glob("*.wav"))[:PER_CLASS]
        if len(files) < PER_CLASS:
            raise SystemExit(f"{raw / label} has only {len(files)} wav files")
        for path in files:
            with wave.open(str(path), "rb") as handle:
                seconds = handle.getnframes() / handle.getframerate()
                rate = handle.getframerate()
            items.append({
                "file": f"{path.stem.removeprefix('New_')}.wav",
                "label": label,
                "recording_id": path.stem,
                "seconds": round(seconds, 3),
                "source": str(path),
                "sample_rate_hz": rate,
            })
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="verify pwa/samples is up to date, write nothing")
    args = parser.parse_args(argv)

    items = pick(RAW)
    if args.check:
        index_path = OUT / "index.json"
        if not index_path.exists():
            raise SystemExit(f"{index_path} is missing; run without --check")
        existing = json.loads(index_path.read_text(encoding="utf-8"))
        if [item["file"] for item in existing["items"]] != [item["file"] for item in items]:
            raise SystemExit(f"{index_path} is stale; run without --check")
        for item in existing["items"]:
            path = OUT / item["file"]
            if not path.exists():
                raise SystemExit(f"{path} is missing; run without --check")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            if digest != item["sha256"][:16]:
                raise SystemExit(f"{path} does not match index.json; re-run")
        print(f"{index_path} is up to date ({len(existing['items'])} clips)")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.wav"):
        stale.unlink()
    for item in items:
        source = Path(item.pop("source"))
        target = OUT / item["file"]
        shutil.copyfile(source, target)
        item["bytes"] = target.stat().st_size
        item["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()

    index = {
        "note": "Selection is the first two recordings per class, sorted by file name. "
                "No prediction was consulted when choosing them.",
        "class_order": list(CLASSES),
        "items": items,
    }
    (OUT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    total = sum(item["bytes"] for item in items)
    print(f"wrote {len(items)} clips to {OUT} ({total / 1024:.0f} KB)")
    for item in items:
        print(f"  {item['label']:4s} {item['file']:20s} {item['seconds']:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
