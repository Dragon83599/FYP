"""Lesson 14: log-mel features, using tools/ai/hs_features.py.

Run from the repository root:
    .venv\\Scripts\\python.exe learning\\lesson14_log_mel.py

The chain taught here is the one the baseline model actually sees:
    2 s window -> band-pass -> frame -> Hann -> rFFT -> power
    -> 40-band mel filterbank -> log10 -> summary -> 128 features
"""

import csv
import json
import sys
from pathlib import Path

import numpy as np

# Put the repository root on the import path so we reuse the real feature code.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ai.hs_features import (  # noqa: E402
    design_bandpass_fir,
    filtfilt,
    frame_signal,
    hz_to_mel,
    log_mel_spectrogram,
    mel_filterbank,
    mel_to_hz,
    read_wav_mono,
    window_feature_vector,
)

N_FFT = 256
HOP_LENGTH = 100
N_MELS = 40
FMIN = 20.0
FMAX = 1000.0
WINDOW_SEC = 2.0


def load_recordings():
    path = Path("data/processed/circor-subset/recordings.csv")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [r for r in rows if Path(r["tsv_path"]).exists()]


def two_second_window(row):
    """Read a recording, band-pass it, and return the first 2 s window."""
    signal, sample_rate = read_wav_mono(row["wav_path"])
    taps = design_bandpass_fir(sample_rate, low_hz=FMIN, high_hz=FMAX, num_taps=257)
    signal = filtfilt(signal, taps)
    window_samples = int(round(WINDOW_SEC * sample_rate))
    return signal[:window_samples], sample_rate


print("=" * 68)
print("Part 1: the chain from waveform to 128 numbers")
print("=" * 68)

chain = [
    ("2 s window", "8000 samples at 4 kHz"),
    ("band-pass", "20-1000 Hz, 257 taps, zero phase"),
    ("frame", f"n_fft={N_FFT} (64 ms), hop={HOP_LENGTH} (25 ms)"),
    ("Hann + rFFT", f"{N_FFT // 2 + 1} frequency bins up to 2000 Hz"),
    ("power", "|X|^2, real numbers only"),
    ("mel filterbank", f"{N_MELS} bands, {FMIN:.0f}-{FMAX:.0f} Hz"),
    ("log10", "compress the dynamic range"),
    ("summary", "mean + std + p90 per band, plus timeline stats"),
    ("feature vector", "128 numbers per 2 s window"),
]
for stage, detail in chain:
    print(f"  {stage:16s} {detail}")
print()

print("=" * 68)
print("Part 2: prepare a real 2 s window")
print("=" * 68)

rows = load_recordings()
abnormal = next(r for r in rows if r["label"] == "1")
normal = next(r for r in rows if r["label"] == "0")

abnormal_window, sample_rate = two_second_window(abnormal)
normal_window, normal_rate = two_second_window(normal)

print(f"abnormal record = {abnormal['record_id']}  label={abnormal['label']}  "
      f"murmur={abnormal['murmur']}")
print(f"normal   record = {normal['record_id']}  label={normal['label']}  "
      f"murmur={normal['murmur']}")
print(f"sample rate     = {sample_rate} Hz")
print(f"window samples  = {len(abnormal_window)}")
print(f"window RMS      = {np.sqrt(np.mean(abnormal_window**2)):.6f}")
print()

print("=" * 68)
print("Part 3: frame, window, and FFT")
print("=" * 68)

frames = frame_signal(abnormal_window, N_FFT, HOP_LENGTH)
frame_window = np.hanning(N_FFT).astype(np.float32)
spectrum = np.fft.rfft(frames * frame_window, n=N_FFT, axis=1)
power = np.abs(spectrum) ** 2
frequencies = np.fft.rfftfreq(N_FFT, d=1.0 / sample_rate)

print(f"frames            = {frames.shape}  (frame index, sample in frame)")
print(f"frame step        = {N_FFT / sample_rate * 1000:.1f} ms, "
      f"hop = {HOP_LENGTH / sample_rate * 1000:.1f} ms")
print(f"rFFT bins         = {spectrum.shape[1]}  (0 Hz to {frequencies[-1]:.0f} Hz)")
print(f"bin spacing       = {frequencies[1]:.2f} Hz")
print(f"power shape       = {power.shape}")
print()

print("=" * 68)
print("Part 4: the mel filterbank")
print("=" * 68)

mel_points = np.linspace(hz_to_mel(FMIN), hz_to_mel(FMAX), N_MELS + 2)
mel_edges_hz = mel_to_hz(mel_points)
filters = mel_filterbank(sample_rate, N_FFT, N_MELS, FMIN, FMAX)

print(f"filterbank shape  = {filters.shape}  (mel band, fft bin)")
print(f"covers            = {filters.shape[1]} fft bins (0 Hz to {frequencies[-1]:.0f} Hz)")
print()
print("band  low_Hz   center_Hz   high_Hz   width_Hz")
for index in (0, 1, 2, 19, 38, 39):
    low, center, high = mel_edges_hz[index], mel_edges_hz[index + 1], mel_edges_hz[index + 2]
    print(f"  {index:2d}  {low:7.1f}   {center:9.1f}   {high:7.1f}   {high - low:8.1f}")
print()

# Each mel band spans edges i .. i+2, so its width is not np.diff(edges).
band_widths = mel_edges_hz[2:] - mel_edges_hz[:-2]
linear_width = (FMAX - FMIN) / N_MELS
print(f"mel band widths   = {band_widths.min():.1f} Hz (band 0) to "
      f"{band_widths.max():.1f} Hz (band {N_MELS - 1})")
print(f"linear 40 bands   = {linear_width:.1f} Hz each, if we skipped mel")
print("over 20-1000 Hz mel is only mildly non-linear (width x"
      f"{band_widths.max() / band_widths.min():.1f}); the effect is larger when the")
print("range spans several octaves, like speech at 20 Hz to 8 kHz.")
print()

print("=" * 68)
print("Part 5: log-mel spectrogram")
print("=" * 68)

mel = log_mel_spectrogram(
    abnormal_window, sample_rate, n_fft=N_FFT, hop_length=HOP_LENGTH,
    n_mels=N_MELS, fmin=FMIN, fmax=FMAX,
)

print(f"log-mel shape     = {mel.shape}  (frame, mel band)")
print(f"value range       = {mel.min():.2f} to {mel.max():.2f} (log10 power)")
print(f"dynamic range     = {mel.max() - mel.min():.2f} decades")
print()
print("mean energy per mel band (first 5 and last 5 bands):")
band_means = mel.mean(axis=0)
for index in list(range(5)) + list(range(N_MELS - 5, N_MELS)):
    print(f"  band {index:2d}  center={mel_edges_hz[index + 1]:7.1f} Hz  "
          f"log-mel mean={band_means[index]:7.3f}")
print()

print("=" * 68)
print("Part 6: 128-number feature vector")
print("=" * 68)

feature = window_feature_vector(abnormal_window, sample_rate)
per_band = 3 * N_MELS
print(f"feature vector    = {feature.shape}  (= {len(feature)} numbers)")
print(f"  per-band summary : {per_band}  ({N_MELS} bands x [mean, std, p90])")
print(f"  spectral moments : 5")
print(f"  time statistics  : 3  (rms, zero-crossing rate, crest factor)")
print(f"  total            : {per_band + 5 + 3}")
print()
print(f"first 6 values    = {np.round(feature[:6], 4).tolist()}")
print(f"last 6 values     = {np.round(feature[-6:], 4).tolist()}")
print()

print("=" * 68)
print("Part 7: abnormal vs normal, same pipeline")
print("=" * 68)

abnormal_feature = window_feature_vector(abnormal_window, sample_rate)
normal_feature = window_feature_vector(normal_window, sample_rate)
cosine = float(
    np.dot(abnormal_feature, normal_feature)
    / (np.linalg.norm(abnormal_feature) * np.linalg.norm(normal_feature))
)

print(f"abnormal RMS      = {np.sqrt(np.mean(abnormal_window**2)):.6f}")
print(f"normal   RMS      = {np.sqrt(np.mean(normal_window**2)):.6f}")
print(f"cosine similarity = {cosine:.4f}  (1.0 = identical direction)")
print(f"mean |difference| = {np.mean(np.abs(abnormal_feature - normal_feature)):.4f}")
print()
print("one 2 s window per patient is not enough to compare - this is a demo,")
print("not a result. Patient-level aggregation comes in lesson 16.")
print()

print("=" * 68)
print("Part 8: save artifacts and automatic checks")
print("=" * 68)

output_dir = Path("learning/outputs/lesson14")
output_dir.mkdir(parents=True, exist_ok=True)

mel_path = output_dir / "log_mel.csv"
np.savetxt(
    mel_path,
    mel,
    delimiter=",",
    header=",".join(f"mel_{i:02d}" for i in range(N_MELS)),
    comments="",
    fmt="%.5f",
)

feature_path = output_dir / "feature_vector.csv"
np.savetxt(
    feature_path,
    np.column_stack([np.arange(len(feature)), feature]),
    delimiter=",",
    header="index,value",
    comments="",
    fmt="%d,%.6f",
)

bands_path = output_dir / "mel_band_edges.csv"
np.savetxt(
    bands_path,
    np.column_stack([np.arange(len(mel_edges_hz)), mel_edges_hz]),
    delimiter=",",
    header="edge_index,axis_hz",
    comments="",
    fmt="%d,%.4f",
)

summary = {
    "abnormal_record": abnormal["record_id"],
    "normal_record": normal["record_id"],
    "sample_rate": int(sample_rate),
    "window_samples": int(len(abnormal_window)),
    "n_fft": N_FFT,
    "hop_length": HOP_LENGTH,
    "n_mels": N_MELS,
    "fmin_hz": FMIN,
    "fmax_hz": FMAX,
    "log_mel_shape": list(mel.shape),
    "feature_length": int(len(feature)),
    "abnormal_vs_normal_cosine": round(cosine, 6),
}
summary_path = output_dir / "summary.json"
summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

print(f"saved log-mel     = {mel_path}")
print(f"saved feature     = {feature_path}")
print(f"saved mel edges   = {bands_path}")
print(f"saved summary     = {summary_path}")
print()

checks = [
    ("window is 8000 samples", len(abnormal_window) == 8000),
    ("mel has 40 bands", mel.shape[1] == N_MELS),
    ("feature length is 128", len(feature) == 128),
    ("mel edges start at 20 Hz", abs(float(mel_edges_hz[0]) - FMIN) < 1e-6),
    ("mel edges end at 1000 Hz", abs(float(mel_edges_hz[-1]) - FMAX) < 1e-6),
]
for name, passed in checks:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")

if not all(passed for _, passed in checks):
    raise SystemExit("one or more checks failed")

print()
print("Lesson 14 finished. See learning/outputs/lesson14/ for the saved numbers.")
