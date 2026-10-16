"""Dependency-light DSP and feature extraction for heart-sound windows.

Only NumPy and the Python standard library are required. The same 20-1000 Hz
band limit, 4 kHz sampling rate and 2 s window are used by the protocol
baseline, so the features stay consistent with ``docs/phase0-interface-freeze.md``.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

EPS = 1e-8


def read_wav_mono(path: str | Path) -> tuple[np.ndarray, int]:
    """Read a PCM WAV file and return float32 mono samples in [-1, 1]."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        n_frames = handle.getnframes()
        raw = handle.readframes(n_frames)

    if sample_width != 2:
        raise ValueError(f"{path}: expected 16-bit PCM, got {sample_width * 8}-bit")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


def design_bandpass_fir(
    sample_rate: int, low_hz: float = 20.0, high_hz: float = 1000.0, num_taps: int = 257
) -> np.ndarray:
    """Windowed-sinc FIR band-pass, normalized to unit gain at the geometric center."""
    if num_taps % 2 == 0:
        num_taps += 1
    n = np.arange(num_taps, dtype=np.float64)
    center = (num_taps - 1) / 2.0

    def lowpass(cutoff_hz: float) -> np.ndarray:
        normalized = cutoff_hz / sample_rate
        return 2.0 * normalized * np.sinc(2.0 * normalized * (n - center))

    taps = lowpass(high_hz) - lowpass(low_hz)
    taps *= np.hamming(num_taps)
    center_hz = float(np.sqrt(low_hz * high_hz))
    response = np.sum(taps * np.exp(-2j * np.pi * center_hz * n / sample_rate))
    return (taps / max(abs(response), EPS)).astype(np.float64)


def filtfilt(signal: np.ndarray, taps: np.ndarray) -> np.ndarray:
    """Zero-phase FIR filtering using symmetric forward/backward convolution."""
    pad = min(len(taps) * 3, max(0, len(signal) - 1))
    if pad == 0:
        return signal.astype(np.float32)
    padded = np.pad(signal, (pad, pad), mode="edge")
    forward = np.convolve(padded, taps, mode="same")
    backward = np.convolve(forward[::-1], taps, mode="same")[::-1]
    return backward[pad:-pad].astype(np.float32)


def frame_signal(signal: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    if len(signal) < frame_length:
        signal = np.pad(signal, (0, frame_length - len(signal)))
    n_frames = 1 + (len(signal) - frame_length) // hop_length
    strides = (signal.strides[0] * hop_length, signal.strides[0])
    return np.lib.stride_tricks.as_strided(
        signal, shape=(n_frames, frame_length), strides=strides, writeable=False
    )


def hz_to_mel(freq: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(freq) / 700.0)


def mel_to_hz(mel: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(mel) / 2595.0) - 1.0)


def mel_filterbank(
    sample_rate: int, n_fft: int, n_mels: int = 40, fmin: float = 20.0, fmax: float = 1000.0
) -> np.ndarray:
    fmax = min(fmax, sample_rate / 2.0)
    mel_points = np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    bins = np.clip(bins, 0, n_fft // 2)

    filters = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float64)
    for index in range(n_mels):
        left, center, right = bins[index], bins[index + 1], bins[index + 2]
        if center == left:
            center = min(left + 1, n_fft // 2)
        if right == center:
            right = min(center + 1, n_fft // 2)
        if right <= center or center <= left:
            continue
        filters[index, left:center] = (np.arange(left, center) - left) / (center - left)
        filters[index, center:right] = (right - np.arange(center, right)) / (right - center)
    return filters


def log_mel_spectrogram(
    signal: np.ndarray,
    sample_rate: int,
    n_fft: int = 256,
    hop_length: int = 100,
    n_mels: int = 40,
    fmin: float = 20.0,
    fmax: float = 1000.0,
) -> np.ndarray:
    frames = frame_signal(signal, n_fft, hop_length)
    window = np.hanning(n_fft).astype(np.float32)
    spectrum = np.fft.rfft(frames * window, n=n_fft, axis=1)
    power = (np.abs(spectrum) ** 2).astype(np.float32)
    mel = power @ mel_filterbank(sample_rate, n_fft, n_mels, fmin, fmax).T
    return np.log10(np.maximum(mel, 1e-10)).astype(np.float32)


def spectral_moments(signal: np.ndarray, sample_rate: int, n_fft: int = 256) -> np.ndarray:
    frames = frame_signal(signal, n_fft, n_fft // 2)
    window = np.hanning(n_fft).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(frames * window, n=n_fft, axis=1)) + EPS
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    centroid = np.sum(spectrum * freqs, axis=1) / np.sum(spectrum, axis=1)
    bandwidth = np.sqrt(
        np.sum(spectrum * (freqs - centroid[:, None]) ** 2, axis=1) / np.sum(spectrum, axis=1)
    )
    cumulative = np.cumsum(spectrum, axis=1)
    rolloff_index = (cumulative >= 0.85 * cumulative[:, -1:]).argmax(axis=1)
    rolloff = freqs[rolloff_index]
    return np.array(
        [
            float(np.mean(centroid)),
            float(np.std(centroid)),
            float(np.mean(bandwidth)),
            float(np.mean(rolloff)),
            float(np.max(rolloff)),
        ],
        dtype=np.float32,
    )


def window_feature_vector(signal: np.ndarray, sample_rate: int) -> np.ndarray:
    """Compact 40-band log-mel summary plus time-domain statistics."""
    normalized = signal - float(np.mean(signal))
    rms = float(np.sqrt(np.mean(normalized**2)) + EPS)
    normalized = normalized / rms

    mel = log_mel_spectrogram(normalized, sample_rate)
    per_band = np.concatenate(
        [
            mel.mean(axis=0),
            mel.std(axis=0),
            np.percentile(mel, 90, axis=0).astype(np.float32),
        ]
    )
    zero_crossings = float(np.mean(np.abs(np.diff(np.signbit(normalized).astype(np.int8)))))
    crest = float(np.max(np.abs(normalized)))
    stats = np.concatenate(
        [
            spectral_moments(normalized, sample_rate),
            np.array([rms, zero_crossings, crest], dtype=np.float32),
        ]
    )
    return np.concatenate([per_band, stats]).astype(np.float32)


def iter_windows(signal: np.ndarray, window_samples: int, hop_samples: int):
    if window_samples <= 0 or hop_samples <= 0:
        raise ValueError("window and hop must be positive")
    if len(signal) < window_samples:
        return
    for start in range(0, len(signal) - window_samples + 1, hop_samples):
        yield start, signal[start : start + window_samples]
