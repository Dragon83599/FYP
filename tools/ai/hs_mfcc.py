"""MFCC + delta + delta-delta features for heart-sound windows (NumPy only).

This module is the feature front-end of the second comparison arm required by
``docs/progress-2026-10-17.md`` (section 5, item 2): "1D CNN / MFCC+delta small
model / 40-band log-mel logistic regression", all evaluated under the same
patient-level 5-fold protocol.

Design decisions (see ``docs/ai-baseline-plan.md`` for the surrounding ADRs):

- The mel filterbank and the log-mel power spectrum are **reused** from
  ``hs_features`` (``mel_filterbank`` / ``log_mel_spectrogram``). Only the DCT
  and the delta regression are new, so the band limits (20-1000 Hz), the 4 kHz
  sample rate and the 40 mel bands stay bit-identical to the log-mel baseline.
- The DCT-II is implemented explicitly with NumPy. SciPy and librosa are not
  dependencies of this repository and adding them would only buy a DCT.
- Per window, the signal is mean-removed and RMS-normalized before the log-mel
  step, mirroring ``hs_features.window_feature_vector``: both comparison arms
  then see gain-normalized input, so an AUROC difference is attributable to the
  feature representation and not to recording level.
- Deltas use the standard regression formula (the same estimator as HTK and
  librosa): ``d[t] = sum_{n=1..W} n * (c[t+n] - c[t-n]) / (2 sum_{n=1..W} n^2)``.
  Boundaries are padded with **odd reflection** (``reflect_type="odd"``, i.e.
  ``c[-n] = 2 c[0] - c[n]``), which is the linear extrapolation implied by the
  locally-linear model of the regression estimator. Edge-replicated padding (the
  HTK/librosa convention) instead halves the delta of a linear ramp on its first
  and last frames, which would bias the mean feature of short windows; odd
  reflection keeps a linear ramp's delta exactly equal to its slope everywhere
  and still returns exactly zero for a constant. The deviation from
  HTK/librosa is therefore limited to the ``W`` boundary frames of each window
  and is documented here on purpose.

All functions are deterministic and finite for silent input: the log-mel floor
in ``hs_features.log_mel_spectrogram`` is ``1e-10``, which maps to ``-10``, and
the DCT of a constant vector is finite by construction.
"""

from __future__ import annotations

import numpy as np

from hs_features import EPS, log_mel_spectrogram

#: Default number of cepstral coefficients kept after the DCT.
DEFAULT_N_MFCC = 13
#: Mel bands, matching the 40-band log-mel logistic-regression baseline.
DEFAULT_N_MELS = 40
#: STFT size in samples (256 @ 4 kHz = 64 ms).
DEFAULT_N_FFT = 256
#: STFT hop in samples (100 @ 4 kHz = 25 ms).
DEFAULT_HOP_LENGTH = 100
#: Regression half-width for the first- and second-order differences.
DEFAULT_DELTA_WIDTH = 2
#: Per-coefficient statistics that make the vector fixed length.
AGGREGATIONS = ("mean", "std", "p90")
#: Order of the feature blocks inside :func:`mfcc_window_vector`.
FEATURE_SETS = ("static", "delta", "delta_delta")


def dct_matrix(n_input: int, n_output: int | None = None, orthonormal: bool = True) -> np.ndarray:
    """Return a DCT-II matrix of shape ``(n_output, n_input)``.

    Applying it as ``coefficients = features @ dct_matrix(n_mels, n_mfcc).T``
    computes ``X[k] = sum_n x[n] cos(pi k (2n+1) / 2N)`` with either the
    orthonormal normalization (default, rows are orthonormal) or the plain
    textbook scaling (``X[0] = sum x[n]``, ``X[k>0] = 2 sum ...``).
    """
    if n_input <= 0:
        raise ValueError("n_input must be positive")
    if n_output is None:
        n_output = n_input
    if n_output <= 0:
        raise ValueError("n_output must be positive")
    k = np.arange(n_output, dtype=np.float64)[:, None]
    n = np.arange(n_input, dtype=np.float64)[None, :]
    basis = np.cos(np.pi * k * (2.0 * n + 1.0) / (2.0 * n_input))
    if orthonormal:
        basis *= np.sqrt(2.0 / n_input)
        basis[0, :] /= np.sqrt(2.0)
    else:
        basis *= 2.0
        basis[0, :] = 1.0
    return basis


def mfcc(
    signal: np.ndarray,
    sample_rate: int,
    n_mfcc: int = DEFAULT_N_MFCC,
    n_fft: int = DEFAULT_N_FFT,
    hop_length: int = DEFAULT_HOP_LENGTH,
    n_mels: int = DEFAULT_N_MELS,
) -> np.ndarray:
    """Return ``(frames, n_mfcc)`` float32 MFCCs of a 1-D signal.

    The signal is *not* gain-normalized here; use :func:`mfcc_window_vector`
    (which normalizes per window) unless you explicitly want raw level in the
    features.
    """
    if n_mfcc <= 0:
        raise ValueError("n_mfcc must be positive")
    log_mel = log_mel_spectrogram(
        np.asarray(signal, dtype=np.float32),
        sample_rate,
        n_fft=n_fft,
        hop_length=hop_length,
        n_mels=n_mels,
    )
    basis = dct_matrix(int(log_mel.shape[1]), n_mfcc, orthonormal=True)
    return np.asarray(log_mel @ basis.T, dtype=np.float32)


def deltas(features: np.ndarray, width: int = DEFAULT_DELTA_WIDTH) -> np.ndarray:
    """Return first-order differences with the standard regression formula.

    ``width`` is the half-width ``W``: ``d[t] = sum_{n=1..W} n (c[t+n] -
    c[t-n]) / (2 sum n^2)``. Boundaries use odd reflection
    (``c[-n] = 2 c[0] - c[n]``), the linear extrapolation implied by the
    estimator's locally-linear model, so the output has the same shape as the
    input, a linear ramp yields a constant equal to its slope (including the
    boundary frames), and a constant input yields exactly zero.
    """
    if width < 1:
        raise ValueError("width must be >= 1")
    values = np.asarray(features, dtype=np.float64)
    squeeze = values.ndim == 1
    if squeeze:
        values = values.reshape(-1, 1)
    if values.ndim != 2:
        raise ValueError("features must be 1-D or 2-D")

    n_frames = values.shape[0]
    padded = np.pad(values, ((width, width), (0, 0)), mode="reflect", reflect_type="odd")
    numerator = np.zeros_like(values)
    denominator = 2.0 * float(sum(n * n for n in range(1, width + 1)))
    for n in range(1, width + 1):
        ahead = padded[width + n : width + n + n_frames, :]
        behind = padded[width - n : width - n + n_frames, :]
        numerator += n * (ahead - behind)
    result = numerator / denominator
    if squeeze:
        result = result[:, 0]
    return np.asarray(result, dtype=np.float32)


def delta_delta(features: np.ndarray, width: int = DEFAULT_DELTA_WIDTH) -> np.ndarray:
    """Second-order differences, i.e. :func:`deltas` applied twice."""
    return deltas(deltas(features, width), width)


def mfcc_feature_dim(n_mfcc: int = DEFAULT_N_MFCC, include_delta_delta: bool = True) -> int:
    """Documented dimension of :func:`mfcc_window_vector`.

    ``n_mfcc * len(AGGREGATIONS) * number_of_feature_sets``
    = ``13 * 3 * 3 = 117`` by default, and ``13 * 3 * 2 = 78`` without
    delta-delta.
    """
    n_sets = len(FEATURE_SETS) if include_delta_delta else len(FEATURE_SETS) - 1
    return int(n_mfcc) * len(AGGREGATIONS) * n_sets


def mfcc_window_vector(
    signal: np.ndarray,
    sample_rate: int,
    n_mfcc: int = DEFAULT_N_MFCC,
    n_fft: int = DEFAULT_N_FFT,
    hop_length: int = DEFAULT_HOP_LENGTH,
    n_mels: int = DEFAULT_N_MELS,
    include_delta_delta: bool = True,
    width: int = DEFAULT_DELTA_WIDTH,
) -> np.ndarray:
    """Fixed-size (``mfcc_feature_dim``) float32 summary of one window.

    Layout, in order: for each feature set in ``("static", "delta",
    "delta_delta")``, the mean, standard deviation and 90th percentile of every
    cepstral coefficient (each block is ``n_mfcc`` long). With the defaults that
    is ``static[mean|std|p90] + delta[mean|std|p90] + delta_delta[mean|std|p90]``
    = ``9 * 13 = 117`` values.

    The window is mean-removed and RMS-normalized first (same as
    ``hs_features.window_feature_vector``), so the vector is gain-invariant.
    Silent windows are therefore not NaN: the log-mel floor produces a constant
    ``-10`` and the deltas of a constant are zero.
    """
    window = np.asarray(signal, dtype=np.float32).ravel()
    if window.size == 0:
        raise ValueError("signal must contain at least one sample")
    centered = window - float(np.mean(window))
    rms = float(np.sqrt(np.mean(centered**2))) + EPS
    normalized = centered / rms

    static = mfcc(
        normalized,
        sample_rate,
        n_mfcc=n_mfcc,
        n_fft=n_fft,
        hop_length=hop_length,
        n_mels=n_mels,
    )
    blocks = [static, deltas(static, width)]
    if include_delta_delta:
        blocks.append(deltas(blocks[-1], width))

    parts: list[np.ndarray] = []
    for block in blocks:
        parts.append(np.asarray(block.mean(axis=0), dtype=np.float64))
        parts.append(np.asarray(block.std(axis=0), dtype=np.float64))
        parts.append(np.asarray(np.percentile(block, 90, axis=0), dtype=np.float64))
    vector = np.concatenate(parts).astype(np.float32)
    expected = mfcc_feature_dim(n_mfcc, include_delta_delta)
    if vector.size != expected:
        raise AssertionError(f"feature vector has {vector.size} values, expected {expected}")
    if not np.all(np.isfinite(vector)):
        raise AssertionError("feature vector contains non-finite values")
    return vector
