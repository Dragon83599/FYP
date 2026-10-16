"""Lesson 13: repository data flow and heart-sound basics (S1/S2, 4 kHz, 2 s windows).

Run from the repository root:
    .venv\\Scripts\\python.exe learning\\lesson13_pcg_dataflow.py

This lesson reads one real CirCor recording and its ``.tsv`` segmentation
annotation, then reproduces the numbers the AI pipeline depends on: the
20-1000 Hz band limit, the 4 kHz sample rate, the 2 s window, and the S1/S2
timing.
"""

import csv
import json
import sys
import wave
from pathlib import Path

import numpy as np

# Put the repository root on the import path so we reuse the real helpers.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ai.hs_segments import (  # noqa: E402
    annotated_fraction,
    annotated_window_starts,
    annotation_labels,
    load_segments,
)

LABEL_NAMES = {0: "unannotated", 1: "S1", 2: "systole", 3: "S2", 4: "diastole"}

print("=" * 68)
print("Part 1: the data flow this lesson explains")
print("=" * 68)

flow = [
    ("probe sensor", "analog anti-alias filter"),
    ("ADC", "8 kHz, 16-bit, 20 ms frame = 160 samples"),
    ("wireless link A", "AUD1 352 B frame / LKA1 fragmentation"),
    ("host de-frame", "CRC check"),
    ("band limit + decimate", "4 kHz edge AI input"),
    ("band-pass", "20-1000 Hz, 257 taps, zero phase"),
    ("window", "2 s window = 8000 samples at 4 kHz"),
    ("features + model", "40-band log-mel, then model"),
    ("result", "window score -> averaged to patient score"),
]
for stage, detail in flow:
    print(f"  {stage:22s} {detail}")
print()

print("=" * 68)
print("Part 2: read one real recording")
print("=" * 68)

recordings_path = Path("data/processed/circor-subset/recordings.csv")
with recordings_path.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

row = next(r for r in rows if Path(r["tsv_path"]).exists())
wav_path = Path(row["wav_path"])
tsv_path = Path(row["tsv_path"])

with wave.open(str(wav_path), "rb") as wav:
    sample_rate = wav.getframerate()
    channels = wav.getnchannels()
    n_frames = wav.getnframes()
    samples = np.frombuffer(wav.readframes(n_frames), dtype="<i2").astype(np.float32)
    samples = samples / 32768.0

duration_s = n_frames / sample_rate
print(f"record_id    = {row['record_id']}")
print(f"subject_id   = {row['subject_id']}   split={row['split']}   label={row['label']}")
print(f"murmur       = {row['murmur']}   outcome={row['outcome']}")
print(f"location     = {row['location']}   (AV/MV/PV/TV = auscultation site)")
print(f"sample rate  = {sample_rate} Hz   channels={channels}   16-bit PCM")
print(f"n_samples    = {n_frames}   duration = {duration_s:.3f} s")
print()

print("=" * 68)
print("Part 3: read the S1/S2 annotation (.tsv)")
print("=" * 68)

segments = load_segments(tsv_path)
print(f"annotation rows = {len(segments)}")
print("first 6 rows (start_seconds, end_seconds, label):")
for start_s, end_s, label in segments[:6]:
    print(f"  {start_s:8.5f}  {end_s:8.5f}  {int(label)}  {LABEL_NAMES.get(int(label), '?')}")
print()

label_values, label_counts = np.unique(segments[:, 2].astype(int), return_counts=True)
print("rows per label:")
for value, count in zip(label_values, label_counts):
    print(f"  {value}  {LABEL_NAMES.get(int(value), '?'):12s} {count}")
print()

s1_starts = segments[segments[:, 2] == 1][:, 0]
s2_starts = segments[segments[:, 2] == 3][:, 0]
rr_all = np.diff(s1_starts)

# A gap between separately annotated blocks creates a huge fake RR interval.
rr = rr_all[(rr_all > 0.3) & (rr_all < 1.5)]
dropped = len(rr_all) - len(rr)

mean_rr = float(np.mean(rr))
heart_rate = 60.0 / mean_rr

# S1 -> S2 gap for beats where an S2 follows an S1 within one cardiac cycle.
s1_s2 = [
    float(s2 - s1)
    for s1 in s1_starts
    for s2 in s2_starts[np.searchsorted(s2_starts, s1) :][:1]
    if 0.05 < float(s2 - s1) < 0.6
]

print(f"S1 count = {len(s1_starts)}   S2 count = {len(s2_starts)}")
print(f"RR intervals kept = {len(rr)} (dropped {dropped} that spanned annotation gaps)")
print(f"mean RR      = {mean_rr:.4f} s")
print(f"heart rate   = {heart_rate:.1f} bpm   (= 60 / mean RR)")
print(f"S1->S2 gap   = {np.mean(s1_s2):.4f} s   (systole width, {len(s1_s2)} beats)")
print()

print("=" * 68)
print("Part 4: 2 s windows and the annotated subset")
print("=" * 68)

window_sec = 2.0
window_samples = int(round(window_sec * sample_rate))
labels_per_sample = annotation_labels(n_frames, segments, sample_rate)
annotated_fraction_all = float(np.count_nonzero(labels_per_sample)) / n_frames

print(f"window length          = {window_samples} samples ({window_sec} s at {sample_rate} Hz)")
print(f"overall annotated part = {annotated_fraction_all:.3f} "
      f"({np.count_nonzero(labels_per_sample)} / {n_frames} samples)")
print()

configs = [
    ("train_baseline default", 2.0, 0, "every window, no cap"),
    ("5-fold CV protocol", 1.0, 4, "hop 1 s, at most 4 windows per recording"),
]
print("window count for this recording:")
print("  hop     unsigned_windows   capped_windows   used_by")
for name, hop_sec, cap, note in configs:
    hop_samples = int(round(hop_sec * sample_rate))
    total = 1 + (n_frames - window_samples) // hop_samples
    capped = min(total, cap) if cap else total
    print(f"  {hop_sec:.0f} s     {total:>8d}          {capped:>8d}        {name} ({note})")
print()

hop_sec = 1.0
hop_samples = int(round(hop_sec * sample_rate))
kept_starts = annotated_window_starts(
    n_frames, segments, sample_rate, window_samples, hop_samples, min_annotated_fraction=0.5
)

window_rows = []
kept_set = set(kept_starts.tolist())
total_windows = 1 + (n_frames - window_samples) // hop_samples

print(f"all {total_windows} windows (hop 1 s, annotated fraction >= 0.5 kept):")
print("  index  start_s    S1   S2   annotated_fraction   kept")
for start in range(0, n_frames - window_samples + 1, hop_samples):
    index = len(window_rows)
    start_s = start / sample_rate
    end_s = start_s + window_sec
    s1_in = int(np.count_nonzero((s1_starts >= start_s) & (s1_starts < end_s)))
    s2_in = int(np.count_nonzero((s2_starts >= start_s) & (s2_starts < end_s)))
    frac = annotated_fraction(labels_per_sample, start, window_samples)
    kept = start in kept_set
    print(f"  {index:5d}  {start_s:6.2f}   {s1_in:2d}   {s2_in:2d}   {frac:17.3f}   {kept}")
    window_rows.append(
        {
            "index": index,
            "start_s": round(start_s, 4),
            "s1_count": s1_in,
            "s2_count": s2_in,
            "annotated_fraction": round(frac, 4),
            "kept": kept,
        }
    )
print()

print(f"all windows (hop 1 s)      = {total_windows}")
print(f"annotated windows kept     = {len(kept_starts)}")
print(f"skipped as unannotated     = {total_windows - len(kept_starts)}")
print()

print("=" * 68)
print("Part 5: save artifacts and automatic checks")
print("=" * 68)

output_dir = Path("learning/outputs/lesson13")
output_dir.mkdir(parents=True, exist_ok=True)

segments_path = output_dir / "segments.csv"
np.savetxt(
    segments_path,
    segments,
    delimiter=",",
    header="start_seconds,end_seconds,label",
    comments="",
    fmt="%.5f",
)

windows_path = output_dir / "windows.csv"
with windows_path.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(window_rows[0].keys()))
    writer.writeheader()
    writer.writerows(window_rows)

summary = {
    "record_id": row["record_id"],
    "subject_id": row["subject_id"],
    "split": row["split"],
    "label": int(row["label"]),
    "outcome": row["outcome"],
    "sample_rate": int(sample_rate),
    "duration_s": round(duration_s, 4),
    "n_samples": int(n_frames),
    "s1_count": int(len(s1_starts)),
    "s2_count": int(len(s2_starts)),
    "mean_rr_s": round(mean_rr, 4),
    "heart_rate_bpm": round(heart_rate, 2),
    "mean_s1_s2_s": round(float(np.mean(s1_s2)), 4),
    "annotated_fraction_all": round(annotated_fraction_all, 4),
    "total_windows_hop_1s": int(total_windows),
    "kept_annotated_windows": int(len(kept_starts)),
}
summary_path = output_dir / "summary.json"
summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

print(f"saved segments = {segments_path}")
print(f"saved windows  = {windows_path}")
print(f"saved summary  = {summary_path}")
print()

checks = [
    ("sample rate is 4000 Hz", sample_rate == 4000),
    ("recording has S1 and S2 labels", len(s1_starts) > 0 and len(s2_starts) > 0),
    ("annotated part is not empty", annotated_fraction_all > 0.0),
    ("heart rate in 40-200 bpm", 40.0 <= heart_rate <= 200.0),
    ("at least one annotated 2 s window", len(kept_starts) > 0),
]
for name, passed in checks:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")

if not all(passed for _, passed in checks):
    raise SystemExit("one or more checks failed")

print()
print("Lesson 13 finished. See learning/outputs/lesson13/ for the saved numbers.")
