"""Lesson 12: FIR band-pass filter.

Run from the repository root:
    .venv\\Scripts\\python.exe learning\\lesson12_fir_bandpass.py

Idea
----
An FIR filter is just a list of numbers called taps. Filtering means a
weighted moving sum of the signal with those taps. A windowed-sinc design
turns a wanted frequency band into that list of taps.

Demo band is 20-200 Hz so the effect is easy to see. The real heart-sound
band is 20-1000 Hz, the same band used by ``tools/ai/hs_features.py``.
"""

import json
import sys
import wave
from pathlib import Path

import numpy as np

# Put the repository root on the import path, then reuse the real pipeline filter.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ai.hs_features import design_bandpass_fir, filtfilt  # noqa: E402


def lowpass_taps(cutoff_hz: float, sample_rate: int, num_taps: int) -> np.ndarray:
    """Ideal low-pass impulse response: 2*fc/fs * sinc(2*fc/fs * (n - center))."""
    if num_taps % 2 == 0:
        num_taps += 1
    n = np.arange(num_taps, dtype=np.float64)
    center = (num_taps - 1) / 2.0
    normalized = cutoff_hz / sample_rate
    return 2.0 * normalized * np.sinc(2.0 * normalized * (n - center))


def bandpass_taps(
    low_hz: float, high_hz: float, sample_rate: int, num_taps: int
) -> np.ndarray:
    """Band-pass = low-pass(high) - low-pass(low), windowed and normalized."""
    taps = lowpass_taps(high_hz, sample_rate, num_taps) - lowpass_taps(
        low_hz, sample_rate, num_taps
    )
    taps = taps * np.hamming(len(taps))
    n = np.arange(len(taps), dtype=np.float64)
    center_hz = float(np.sqrt(low_hz * high_hz))
    response = np.sum(taps * np.exp(-2j * np.pi * center_hz * n / sample_rate))
    return (taps / max(abs(response), 1e-8)).astype(np.float64)


def frequency_gain(taps: np.ndarray, sample_rate: int, freq_hz: float) -> float:
    """|H(f)|: how much a sine at freq_hz is scaled by this filter."""
    n = np.arange(len(taps), dtype=np.float64)
    return float(abs(np.sum(taps * np.exp(-2j * np.pi * freq_hz * n / sample_rate))))


def apply_causal(signal: np.ndarray, taps: np.ndarray) -> np.ndarray:
    """Real-time convolution: output is delayed by (len(taps) - 1) / 2 samples."""
    return np.convolve(signal, taps, mode="full").astype(np.float32)


def apply_zero_phase(signal: np.ndarray, taps: np.ndarray) -> np.ndarray:
    """Forward then backward pass: same shape, no net delay."""
    pad = min(len(taps) * 3, max(0, len(signal) - 1))
    if pad == 0:
        return signal.astype(np.float32)
    padded = np.pad(signal, (pad, pad), mode="edge")
    forward = np.convolve(padded, taps, mode="same")
    backward = np.convolve(forward[::-1], taps, mode="same")[::-1]
    return backward[pad:-pad].astype(np.float32)


def spectrum(signal: np.ndarray, sample_rate: int):
    """Return (frequencies, magnitudes) of a Hann-windowed rFFT."""
    window = np.hanning(len(signal))
    magnitudes = np.abs(np.fft.rfft(signal * window))
    frequencies = np.fft.rfftfreq(len(signal), d=1.0 / sample_rate)
    return frequencies, magnitudes


def peak_frequencies(frequencies, magnitudes, count: int, low_hz=None, high_hz=None):
    """Top `count` peaks, optionally limited to a frequency band."""
    mask = np.ones(len(frequencies), dtype=bool)
    if low_hz is not None:
        mask &= frequencies >= low_hz
    if high_hz is not None:
        mask &= frequencies <= high_hz
    band_index = np.flatnonzero(mask)
    order = band_index[np.argsort(magnitudes[band_index])[-count:][::-1]]
    return [(float(frequencies[i]), float(magnitudes[i])) for i in order]


def gain_bar(value: float, full_scale: float, width: int = 32) -> str:
    filled = int(round(min(max(value / full_scale, 0.0), 1.0) * width))
    return "#" * filled


print("=" * 64)
print("Part 1: design a 20-200 Hz band-pass filter")
print("=" * 64)

demo_rate = 4000
demo_taps = bandpass_taps(20.0, 200.0, demo_rate, num_taps=257)
print(f"number of taps     = {len(demo_taps)}")
print(f"causal delay       = {(len(demo_taps) - 1) / 2:.0f} samples "
      f"= {(len(demo_taps) - 1) / 2 / demo_rate * 1000:.1f} ms (real-time path only)")
print()
print("filter gain at test frequencies:")
for freq in (10.0, 20.0, 50.0, 120.0, 200.0, 400.0, 800.0):
    gain = frequency_gain(demo_taps, demo_rate, freq)
    print(f"  {freq:6.1f} Hz  gain={gain:6.3f}  {gain_bar(gain, 1.0)}")
print()

print("=" * 64)
print("Part 2: build a signal and filter it")
print("=" * 64)

demo_time = np.arange(demo_rate) / demo_rate
demo_signal = (
    0.6 * np.sin(2.0 * np.pi * 50.0 * demo_time)
    + 0.3 * np.sin(2.0 * np.pi * 120.0 * demo_time)
    + 0.1 * np.sin(2.0 * np.pi * 400.0 * demo_time)
)
demo_filtered = apply_zero_phase(demo_signal, demo_taps)

before_freqs, before_mags = spectrum(demo_signal, demo_rate)
after_freqs, after_mags = spectrum(demo_filtered, demo_rate)


def magnitude_at(frequencies, magnitudes, freq_hz):
    return float(magnitudes[np.argmin(np.abs(frequencies - freq_hz))])


print("magnitude at the three known frequencies:")
print("  freq     before      after   change")
for freq in (50.0, 120.0, 400.0):
    before = magnitude_at(before_freqs, before_mags, freq)
    after = magnitude_at(after_freqs, after_mags, freq)
    print(f"  {freq:5.0f} Hz {before:9.3f} {after:10.3f}   {before / max(after, 1e-12):8.1f}x")
print()

before_400 = magnitude_at(before_freqs, before_mags, 400.0)
after_400 = magnitude_at(after_freqs, after_mags, 400.0)

# A single click at sample 1000 makes the delay visible.
click = np.zeros(demo_rate, dtype=np.float64)
click[1000] = 1.0
click_causal = apply_causal(click, demo_taps)
click_zero_phase = apply_zero_phase(click, demo_taps)
print("click placed at sample 1000:")
print(f"  real-time (causal) peak at sample {int(np.argmax(np.abs(click_causal)))}"
      f"  -> delayed by {(len(demo_taps) - 1) // 2} samples")
print("  zero-phase peak         at sample "
      f"{int(np.argmax(np.abs(click_zero_phase)))}  -> no delay")
print()

print("=" * 64)
print("Part 3: real heart sound, 20-1000 Hz band-pass")
print("=" * 64)

wav_path = Path("data/raw/circor/subset/training_data/9979_AV.wav")
with wave.open(str(wav_path), "rb") as wav:
    real_rate = wav.getframerate()
    real_samples = (
        np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32)
        / 32768.0
    )

segment = real_samples[:real_rate]
repo_taps = design_bandpass_fir(real_rate, low_hz=20.0, high_hz=1000.0, num_taps=257)
our_taps = bandpass_taps(20.0, 1000.0, real_rate, num_taps=257)
tap_difference = float(np.max(np.abs(repo_taps - our_taps)))

real_filtered = filtfilt(segment, repo_taps)
rms_before = float(np.sqrt(np.mean(segment**2)))
rms_after = float(np.sqrt(np.mean(real_filtered**2)))

print(f"sample rate        = {real_rate} Hz")
print(f"samples in segment = {len(segment)}")
print(f"max |repo - ours|  = {tap_difference:.3e} (our design reproduces the repo filter)")
print(f"RMS before = {rms_before:.6f}   RMS after = {rms_after:.6f}")
print()

real_before_freqs, real_before_mags = spectrum(segment, real_rate)
real_after_freqs, real_after_mags = spectrum(real_filtered, real_rate)
for label, freqs, mags in (
    ("before", real_before_freqs, real_before_mags),
    ("after ", real_after_freqs, real_after_mags),
):
    print(f"{label} filtering, top 5 peaks in 20-1000 Hz:")
    for freq, magnitude in peak_frequencies(freqs, mags, 5, low_hz=20.0, high_hz=1000.0):
        print(f"  {freq:7.2f} Hz  magnitude={magnitude:9.3f}")
    print()

print("=" * 64)
print("Part 4: save artifacts")
print("=" * 64)

output_dir = Path("learning/outputs/lesson12")
output_dir.mkdir(parents=True, exist_ok=True)

response_path = output_dir / "demo_filter_response.csv"
response_freqs = np.linspace(0.0, 1000.0, 501)
response_gains = np.array(
    [frequency_gain(demo_taps, demo_rate, float(f)) for f in response_freqs]
)
np.savetxt(
    response_path,
    np.column_stack([response_freqs, response_gains]),
    delimiter=",",
    header="freq_hz,gain",
    comments="",
    fmt="%.6f",
)

checks = {
    "gain_50hz": frequency_gain(demo_taps, demo_rate, 50.0),
    "gain_120hz": frequency_gain(demo_taps, demo_rate, 120.0),
    "gain_400hz": frequency_gain(demo_taps, demo_rate, 400.0),
    "stopband_reduction_400hz": before_400 / max(after_400, 1e-12),
}
summary = {
    "demo_band_hz": [20.0, 200.0],
    "demo_num_taps": int(len(demo_taps)),
    "real_band_hz": [20.0, 1000.0],
    "real_sample_rate": int(real_rate),
    "real_rms_before": rms_before,
    "real_rms_after": rms_after,
    "repo_vs_ours_max_tap_difference": tap_difference,
    "checks": checks,
}
summary_path = output_dir / "summary.json"
summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

spectrum_path = output_dir / "real_spectrum_before_after.csv"
np.savetxt(
    spectrum_path,
    np.column_stack([real_before_freqs, real_before_mags, real_after_mags]),
    delimiter=",",
    header="freq_hz,before,after",
    comments="",
    fmt="%.6f",
)

print(f"saved filter response = {response_path}")
print(f"saved summary         = {summary_path}")
print(f"saved real spectrum   = {spectrum_path}")
print()

print("=" * 64)
print("Part 5: automatic checks")
print("=" * 64)

assertions = [
    ("50 Hz stays (gain >= 0.9)", checks["gain_50hz"] >= 0.9),
    ("120 Hz stays (gain >= 0.9)", checks["gain_120hz"] >= 0.9),
    ("400 Hz removed (gain <= 0.05)", checks["gain_400hz"] <= 0.05),
    ("400 Hz cut by more than 100x", checks["stopband_reduction_400hz"] > 100.0),
]
for name, passed in assertions:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")

if not all(passed for _, passed in assertions):
    raise SystemExit("one or more checks failed")

print()
print("Lesson 12 finished. See learning/outputs/lesson12/ for the saved numbers.")
