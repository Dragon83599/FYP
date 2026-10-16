"""Lesson 11: FFT and spectrum.

Run from the repository root:
    .venv\\Scripts\\python.exe learning\\lesson11_fft_spectrum.py
"""

from pathlib import Path
import wave

import numpy as np

# Synthetic signal with three known frequencies.
sample_rate = 4000
duration_sec = 1.0
time = np.arange(int(sample_rate * duration_sec)) / sample_rate
signal = (
    0.6 * np.sin(2.0 * np.pi * 50.0 * time)
    + 0.3 * np.sin(2.0 * np.pi * 120.0 * time)
    + 0.1 * np.sin(2.0 * np.pi * 400.0 * time)
)

window = np.ones(len(signal))
spectrum = np.fft.rfft(signal * window)
frequencies = np.fft.rfftfreq(len(signal), d=1.0 / sample_rate)
magnitudes = np.abs(spectrum)

top_indices = np.argsort(magnitudes)[-3:][::-1]
print("synthetic signal top 3 frequencies:")
for index in top_indices:
    print(f"  {frequencies[index]:7.2f} Hz  magnitude={magnitudes[index]:.4f}")
print()

# Real heart-sound recording: first 1 second.
wav_path = Path("data/raw/circor/subset/training_data/9979_AV.wav")
with wave.open(str(wav_path), "rb") as wav:
    real_sample_rate = wav.getframerate()
    samples = np.frombuffer(
        wav.readframes(wav.getnframes()),
        dtype="<i2",
    ).astype(np.float32) / 32768.0

segment = samples[:real_sample_rate]
real_window = np.hanning(len(segment))
real_spectrum = np.fft.rfft(segment * real_window)
real_frequencies = np.fft.rfftfreq(len(segment), d=1.0 / real_sample_rate)
real_magnitudes = np.abs(real_spectrum)

band = (real_frequencies >= 20.0) & (real_frequencies <= 1000.0)
band_frequencies = real_frequencies[band]
band_magnitudes = real_magnitudes[band]

top_real = np.argsort(band_magnitudes)[-5:][::-1]
print("heart sound top frequencies in 20-1000 Hz:")
for index in top_real:
    print(f"  {band_frequencies[index]:7.2f} Hz  magnitude={band_magnitudes[index]:.4f}")
