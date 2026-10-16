#!/usr/bin/env python3
"""Generate a synthetic phonocardiogram dataset for pipeline smoke tests.

The generator is deliberately simple: S1/S2 damped sinusoids, optional
band-limited systolic murmur, background noise and slow baseline wander. It is
only used to validate the training/evaluation/budget harness when the real
dataset is unavailable. Results on this dataset must never be reported as
clinical evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import wave
from pathlib import Path

import numpy as np

from hs_features import design_bandpass_fir, filtfilt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="data/processed/synthetic-pcg")
    parser.add_argument("--subjects", type=int, default=240)
    parser.add_argument("--seed", type=int, default=20261007)
    parser.add_argument("--sample-rate", type=int, default=4000)
    return parser.parse_args()


def damped_tone(time: np.ndarray, frequency: float, decay: float) -> np.ndarray:
    envelope = np.exp(-time / decay)
    return envelope * np.sin(2 * np.pi * frequency * time)


def band_limited_noise(
    rng: np.random.Generator, n_samples: int, sample_rate: int, low: float, high: float
) -> np.ndarray:
    noise = rng.normal(0, 1, n_samples)
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n_samples, d=1.0 / sample_rate)
    spectrum[(freqs < low) | (freqs > high)] = 0
    filtered = np.fft.irfft(spectrum, n=n_samples)
    return filtered / max(np.std(filtered), 1e-9)


def synthesize_recording(
    rng: np.random.Generator,
    sample_rate: int,
    duration_s: float,
    murmur: bool,
    heart_rate_bpm: float,
) -> np.ndarray:
    n_samples = int(round(duration_s * sample_rate))
    time = np.arange(n_samples) / sample_rate
    signal = np.zeros(n_samples, dtype=np.float64)

    rr = 60.0 / heart_rate_bpm
    s1_time = 0.08
    s2_time = 0.30 * rr
    beat_start = 0.0
    s1_amplitude = rng.uniform(0.5, 0.9)
    s2_amplitude = rng.uniform(0.3, 0.6)

    while beat_start < duration_s:
        for center, frequency, decay, amplitude in (
            (beat_start + s1_time, rng.uniform(25, 45), 0.045, s1_amplitude),
            (beat_start + s2_time, rng.uniform(35, 65), 0.035, s2_amplitude),
        ):
            start = int(round(center * sample_rate))
            length = int(round(0.15 * sample_rate))
            if start >= n_samples:
                continue
            segment_time = np.arange(min(length, n_samples - start)) / sample_rate
            signal[start : start + len(segment_time)] += amplitude * damped_tone(
                segment_time, frequency, decay
            )
        if murmur:
            murmur_start = int(round((beat_start + s1_time + 0.04) * sample_rate))
            murmur_end = int(round((beat_start + s2_time - 0.02) * sample_rate))
            if murmur_end > murmur_start:
                length = min(murmur_end, n_samples) - murmur_start
                if length > 0:
                    envelope = np.hanning(length)
                    signal[murmur_start : murmur_start + length] += (
                        0.45 * rng.uniform(0.6, 1.4) * envelope
                        * band_limited_noise(rng, length, sample_rate, 80, 600)
                    )
        beat_start += rr

    background = band_limited_noise(rng, n_samples, sample_rate, 20, 1000)
    wander = np.sin(2 * np.pi * rng.uniform(0.2, 0.8) * time + rng.uniform(0, 6))
    signal = signal + 0.05 * background + 0.03 * wander
    signal *= rng.uniform(0.6, 1.0)
    return np.clip(signal, -1.0, 1.0)


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    wav_dir = out_dir / "training_data"
    rng = random.Random(args.seed)
    np_rng = np.random.default_rng(args.seed)
    locations = ["AV", "MV", "PV", "TV"]

    subjects: list[dict] = []
    recordings: list[dict] = []
    for index in range(args.subjects):
        subject_id = f"{90000 + index}"
        label = 1 if index % 2 == 0 else 0
        murmur = "Present" if label else "Absent"
        heart_rate = rng.uniform(70, 140)
        n_recordings = rng.randint(2, 4)
        chosen_locations = rng.sample(locations, n_recordings)
        total_duration = 0.0
        for location in chosen_locations:
            record_id = f"{subject_id}_{location}"
            duration = rng.uniform(8.0, 20.0)
            samples = synthesize_recording(
                np_rng, args.sample_rate, duration, bool(label), heart_rate
            )
            wav_path = wav_dir / f"{record_id}.wav"
            write_wav(wav_path, samples, args.sample_rate)
            total_duration += duration
            recordings.append(
                {
                    "subject_id": subject_id,
                    "label": label,
                    "murmur": murmur,
                    "outcome": "Abnormal" if label else "Normal",
                    "split": "",
                    "record_id": record_id,
                    "location": location,
                    "wav_path": str(wav_path),
                    "sample_rate": args.sample_rate,
                    "n_samples": len(samples),
                    "channels": 1,
                    "duration_s": round(duration, 3),
                }
            )
        subjects.append(
            {
                "subject_id": subject_id,
                "murmur": murmur,
                "label": label,
                "outcome": "Abnormal" if label else "Normal",
                "age": "Child",
                "sex": "Female" if index % 2 else "Male",
                "campaign": "synthetic",
                "n_recordings": n_recordings,
                "total_duration_s": round(total_duration, 3),
                "split": "",
            }
        )

    split_of = assign_splits(subjects, seed=args.seed)
    for row in subjects:
        row["split"] = split_of[row["subject_id"]]
    for row in recordings:
        row["split"] = split_of[row["subject_id"]]

    write_csv(out_dir / "subjects.csv", subjects, list(subjects[0].keys()))
    write_csv(out_dir / "recordings.csv", recordings, list(recordings[0].keys()))
    summary = {
        "dataset": "synthetic-pcg (pipeline smoke test only, not clinical evidence)",
        "seed": args.seed,
        "subjects": len(subjects),
        "recordings": len(recordings),
        "split_subjects": count_by(subjects, "split"),
        "split_labels": {
            split: count_by([row for row in subjects if row["split"] == split], "label")
            for split in ("train", "val", "test")
        },
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    print(f"[synthetic] wrote {out_dir / 'subjects.csv'}")
    print(f"[synthetic] wrote {out_dir / 'recordings.csv'}")


def assign_splits(subjects: list[dict], seed: int) -> dict[str, str]:
    rng = random.Random(seed + 1)
    split_of: dict[str, str] = {}
    for label in (0, 1):
        rows = [row for row in subjects if row["label"] == label]
        rng.shuffle(rows)
        n_train = int(round(len(rows) * 0.7))
        n_val = int(round(len(rows) * 0.15))
        for index, row in enumerate(rows):
            if index < n_train:
                split = "train"
            elif index < n_train + n_val:
                split = "val"
            else:
                split = "test"
            split_of[row["subject_id"]] = split
    return split_of


def count_by(rows: list[dict], key: str) -> dict:
    counts: dict = {}
    for row in rows:
        value = str(row[key])
        counts[value] = counts.get(value, 0) + 1
    return counts


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
