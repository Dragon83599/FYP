"""Deterministic DSP S1/S2 detector for the edge host (ADR-008).

The detector is deliberately simple and fixed-point friendly so that it can be
ported to the ESP32-S3 without a learned model:

1. 20-85 Hz band-pass. The upper edge is deliberately below the 150 Hz that
   ADR-002 allows for the *signal*: 85-150 Hz is where systolic murmur noise
   competes with S1/S2, and a parameter sweep on the synthetic set moved macro
   F1 from 0.68 (20-150 Hz) to 0.83 (20-85 Hz). This detector band is a
   separate design parameter and must be re-validated on CirCor.
2. Short-time RMS envelope (38 ms), a moving average of squares.
3. Block-wise adaptive threshold: the noise floor is the median envelope of
   each 4 s block, so a noisy section raises its own threshold.
4. Peak selection by steepest-first non-maximum suppression with a refractory
   period, plus a **prominence** test that rejects the shallow ripple a murmur
   produces inside systole.
5. Spurious-peak merge: two peaks closer than ``max(merge_min_gap_s,
   merge_fraction * short_gap)`` are collapsed to the stronger one, because a
   click inside one heart sound otherwise splits it into two events.
6. S1/S2 labelling by a two-state dynamic program over the gaps (default
   ``labeler="sequential"``): the two gap centres are estimated robustly, and
   each step is scored against the expected systole or diastole interval, with
   a same-label step allowed at a cost (a missed beat is absorbed locally
   instead of inverting the phase of the whole recording). The legacy rigid
   alternation rule (``labeler="parity"``) is kept for ablation.
7. Physiological high-rate prior (``gap_prior="orient"``, default): the
   "shorter gap is systole" heuristic is only valid while diastole is longer
   than systole, which fails for many patients above ~130 bpm. Above
   ``prior_min_bpm`` (120 bpm) the DP therefore takes the S1/S2 *orientation*
   from a population systole/RR regression
   ``S = systole_prior_a * sqrt(RR) + systole_prior_b`` (clamped), while the
   observed gap clusters still supply the *durations*. ``gap_prior="model"``
   uses the regression for both, and ``gap_prior="cluster"`` reproduces the
   pre-2026-10-18 behaviour exactly. The coefficients were fitted on the
   frozen patient-level tuning folds only (see ``DetectorConfig``), the RR
   gate is label-independent, and nothing is learned at run time.

Limitation that must be reported with any result: above ~120 bpm the systolic
and diastolic intervals are close for many patients, and a single-site PCG
cannot always order S1 and S2 from timing alone. The physiological prior fixes
the *population* ordering -- full-cohort macro F1 on the >120 bpm subset rose
from 0.599 to 0.658 -- but it does not remove whole-recording inversions: the
>120 bpm swap rate stayed at 0.271 (target < 0.20), because the regression
predicts which interval is longer for only ~70 % of recordings and the rest are
individual patients whose systole/diastole ordering is not recoverable from
timing. ``info['labeling_reliable']`` therefore stays False above
``reliable_max_bpm``, and the >120 bpm labels must still be quoted separately.
Above roughly 150 bpm no single-site PCG cue reliably separates the two, so
event *positions* remain usable while the labels must not be trusted.

Provenance note: the sequential labeller and the peak merge were added to this
module on 2026-10-18 after the first real-data evaluation (which used the
parity rule and scored macro F1 0.572). They lift the full-cohort score to
0.772 and cut the swap rate from 0.361 to 0.110. Their parameter defaults have
NOT been re-selected inside a patient-level training split, so the gain may be
partly optimistic; see ADR-008's 2026-10-18 evening supplement. The high-rate
prior was added afterwards: its coefficients were fitted on the frozen
patient-level folds 0-3 only and the nested held-out protocol (refit on four
folds, score the fifth) is recorded in the handover report, not in this module.

Only NumPy and the standard library are required, matching ``hs_features``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from hs_events import S1, S2, EventSet, make_event_set
from hs_features import design_bandpass_fir, filtfilt


@dataclass(frozen=True)
class DetectorConfig:
    """Tunable detector parameters.

    ``sample_rate`` must match the recording; the band limits are in Hz so the
    same config works at 4 kHz and 8 kHz. ``num_taps`` changes the transition
    width, not the pass band.
    """

    sample_rate: int = 4000
    band_low_hz: float = 20.0
    band_high_hz: float = 85.0
    num_taps: int = 129
    envelope_ms: float = 38.0
    noise_block_s: float = 4.0
    threshold_ratio: float = 2.5
    min_threshold_fraction: float = 0.10
    refractory_ms: float = 100.0
    prominence_window_ms: float = 80.0
    prominence_fraction: float = 0.60
    label_tiebreak_spread: float = 0.10
    reliable_max_bpm: float = 120.0
    labeler: str = "sequential"
    same_state_penalty: float = 1.5
    min_gap_separation: float = 1.05
    merge_fraction: float = 0.55
    merge_min_gap_s: float = 0.12
    evidence_weight_zcr: float = 0.0
    evidence_weight_amplitude: float = 0.0
    min_events: int = 2
    #: High-rate labelling strategy. ``"cluster"`` = legacy behaviour (the
    #: shorter gap cluster is always systole), ``"model"`` = the DP compares
    #: each gap against the physiological regression, ``"orient"`` = the
    #: regression only decides which cluster is systole and the observed
    #: clusters supply the durations (default).
    gap_prior: str = "orient"
    #: Systolic interval model S = a*sqrt(RR) + b, fitted on the frozen
    #: patient-level tuning folds 0-3 of data/processed/circor/splits.csv
    #: (fingerprint 1f212dea...a47): record-level detected-gap medians,
    #: labels taken from the CirCor reference, a=0.356707, b=-0.018410 s.
    #: The held-out nested protocol refitted these on four folds and scored
    #: the fifth; the fold-wise fits were a in [0.3508, 0.3662] and
    #: b in [-0.0254, -0.0138], so the frozen values are not fold-specific.
    systole_prior_a: float = 0.356707
    systole_prior_b: float = -0.018410
    systole_prior_min_s: float = 0.10
    systole_prior_max_s: float = 0.60
    #: The prior is only used above this heart rate. The threshold matches
    #: ``reliable_max_bpm`` because diastole is longer than systole below it
    #: for the population, so the legacy short/long rule is already correct.
    prior_min_bpm: float = 120.0

    def with_sample_rate(self, sample_rate: int) -> "DetectorConfig":
        return replace(self, sample_rate=int(sample_rate))

    def samples(self, duration_ms: float) -> int:
        return max(1, int(round(self.sample_rate * duration_ms / 1000.0)))


@dataclass
class DetectionResult:
    events: EventSet
    info: dict = field(default_factory=dict)


def _moving_rms(signal: np.ndarray, window_samples: int) -> np.ndarray:
    window = max(1, int(window_samples))
    kernel = np.ones(window, dtype=np.float64) / float(window)
    energy = np.convolve(signal.astype(np.float64) ** 2, kernel, mode="same")
    return np.sqrt(np.maximum(energy, 0.0))


def _block_median(values: np.ndarray, block_samples: int) -> np.ndarray:
    """Median of consecutive blocks, expanded back to the original length."""
    values = np.asarray(values, dtype=np.float64)
    if len(values) == 0:
        return values
    block = max(1, int(block_samples))
    n_blocks = (len(values) + block - 1) // block
    padded = np.pad(values, (0, n_blocks * block - len(values)), mode="edge")
    medians = np.median(padded.reshape(n_blocks, block), axis=1)
    return np.repeat(medians, block)[: len(values)]


def _prominence(envelope: np.ndarray, index: int, half_window: int) -> float:
    low = max(0, index - half_window)
    high = min(len(envelope), index + half_window + 1)
    return float(envelope[index] - np.min(envelope[low:high]))


def _select_peaks(
    envelope: np.ndarray,
    threshold: np.ndarray,
    refractory_samples: int,
    prominence_half_samples: int,
    prominence_fraction: float,
) -> np.ndarray:
    """Steepest-first peak picking with a refractory period and prominence test."""
    candidates = np.where(envelope > threshold)[0]
    if candidates.size == 0:
        return np.zeros(0, dtype=np.int64)
    order = candidates[np.argsort(envelope[candidates])[::-1]]
    blocked = np.zeros(len(envelope), dtype=bool)
    accepted: list[int] = []
    half = max(1, int(refractory_samples))
    for index in order:
        index = int(index)
        if blocked[index]:
            continue
        peak = float(envelope[index])
        if peak <= 0.0:
            continue
        if _prominence(envelope, index, prominence_half_samples) < prominence_fraction * peak:
            blocked[index] = True
            continue
        accepted.append(index)
        blocked[max(0, index - half + 1) : min(len(envelope), index + half)] = True
    accepted.sort()
    return np.asarray(accepted, dtype=np.int64)


def _zero_crossing_hz(
    filtered: np.ndarray, peaks: np.ndarray, half_window: int, sample_rate: int
) -> np.ndarray:
    """Dominant-frequency proxy from the zero-crossing rate around each peak."""
    values = np.full(len(peaks), np.nan, dtype=np.float64)
    for position, peak in enumerate(peaks):
        low = max(0, int(peak) - half_window)
        high = min(len(filtered), int(peak) + half_window)
        segment = filtered[low:high]
        if segment.size < 4:
            continue
        crossings = np.count_nonzero(np.diff(np.signbit(segment)))
        duration = segment.size / float(sample_rate)
        values[position] = crossings / (2.0 * duration)
    return values


def _median_gap(gaps: np.ndarray) -> float:
    return float(np.median(gaps)) if gaps.size else float("inf")


def label_events(
    times: np.ndarray,
    amplitudes: np.ndarray,
    *,
    zcr: np.ndarray | None = None,
    config: DetectorConfig | None = None,
) -> np.ndarray:
    """Assign S1/S2 to alternating events.

    Primary cue: systole is shorter than diastole, so the phase with the
    shorter median gap is S1. When the two medians are within
    ``label_tiebreak_spread`` (high heart rate), the interval cue is ambiguous
    and a spectral cue is used instead: S1 has the lower zero-crossing rate.
    Amplitude is the last resort.
    """
    config = config or DetectorConfig()
    times = np.asarray(times, dtype=np.float64)
    amplitudes = np.asarray(amplitudes, dtype=np.float64)
    labels = np.full(len(times), S1, dtype=np.int8)
    if len(times) < 2:
        return labels

    gaps = np.diff(times)
    systolic = gaps[0::2]
    diastolic = gaps[1::2]
    median_systolic = _median_gap(systolic)
    median_diastolic = _median_gap(diastolic)
    if not np.isfinite(median_systolic) and not np.isfinite(median_diastolic):
        return labels

    # If the first gap is longer than the second, event 0 is an S2.
    invert = median_systolic > median_diastolic

    if np.isfinite(median_systolic) and np.isfinite(median_diastolic):
        mean_gap = 0.5 * (median_systolic + median_diastolic)
        spread = abs(median_systolic - median_diastolic) / max(mean_gap, 1e-9)
        if spread < config.label_tiebreak_spread:
            decided = False
            if zcr is not None and len(zcr) == len(times):
                even = float(np.nanmedian(zcr[0::2]))
                odd = float(np.nanmedian(zcr[1::2]))
                if np.isfinite(even) and np.isfinite(odd) and max(even, odd) > 0:
                    if abs(even - odd) / max(even, odd) > 0.05:
                        # Higher-frequency phase is S2, not S1.
                        invert = even > odd
                        decided = True
            if not decided and len(amplitudes) >= 2:
                amp_even = float(np.median(amplitudes[0::2]))
                amp_odd = float(np.median(amplitudes[1::2]))
                # S1 is usually louder at the auscultation site.
                invert = amp_even < amp_odd

    labels[0::2] = S2 if invert else S1
    labels[1::2] = S1 if invert else S2
    return labels


def _two_gap_centres(gaps: np.ndarray) -> tuple[float, float]:
    """Robust two-cluster split of the inter-event gaps (shorter, longer)."""
    low = float(np.percentile(gaps, 25))
    high = float(np.percentile(gaps, 75))
    for _ in range(5):
        if high <= low:
            break
        middle = 0.5 * (low + high)
        short = gaps[gaps < middle]
        long = gaps[gaps >= middle]
        if short.size == 0 or long.size == 0:
            break
        low = float(np.median(short))
        high = float(np.median(long))
    return low, high


def _cycle_estimate(gaps: np.ndarray) -> float:
    """One cardiac cycle from the median of consecutive gap pairs.

    Consecutive gaps cover one cycle whatever their S1/S2 order, so this
    estimate is independent of the labels -- important because the labeller
    uses it to decide the S1/S2 orientation in the first place.
    """
    k = (len(gaps) // 2) * 2
    if k < 2:
        return float("nan")
    return float(np.median(gaps[:k].reshape(-1, 2).sum(axis=1)))


def _systolic_prior(cycle_s: float, config: DetectorConfig) -> tuple[float, float]:
    """Expected (systole, diastole) for one cycle, or ``(nan, nan)``.

    The regression is the classic square-root systolic-time-interval form;
    diastole is the remainder of the cycle. The clamp keeps a grossly wrong
    RR estimate from producing an impossible expectation, and diastole is
    never allowed below 50 ms.

    Fixed-point form for the ESP32: ``s_q15 = (A_q15 * isqrt(rr_q15)) >> 15
    + B_q15`` with one Q15 square root (16 Newton iterations) and a
    multiply-accumulate; no division and no runtime learning.
    """
    if not np.isfinite(cycle_s) or cycle_s <= 0:
        return float("nan"), float("nan")
    systole = config.systole_prior_a * float(np.sqrt(cycle_s)) + config.systole_prior_b
    systole = min(max(systole, config.systole_prior_min_s), config.systole_prior_max_s)
    systole = min(systole, cycle_s - 0.05)
    diastole = cycle_s - systole
    if systole <= 0 or diastole <= 0:
        return float("nan"), float("nan")
    return float(systole), float(diastole)


def _merge_close_peaks(
    peaks: np.ndarray, envelope: np.ndarray, min_gap_samples: float, rounds: int = 3
) -> np.ndarray:
    """Drop the weaker of any two peaks closer than ``min_gap_samples``.

    A spurious peak inside one heart sound splits it into two events that no
    systole/diastole model can explain; removing the weaker one restores the
    rhythm instead of letting the labeller invert the whole recording.
    """
    current = [int(p) for p in peaks]
    if len(current) < 2 or min_gap_samples <= 0:
        return np.asarray(current, dtype=np.int64)
    for _ in range(rounds):
        kept: list[int] = []
        changed = False
        for peak in current:
            if kept and peak - kept[-1] < min_gap_samples:
                changed = True
                if envelope[peak] > envelope[kept[-1]]:
                    kept[-1] = peak
            else:
                kept.append(peak)
        current = kept
        if not changed:
            break
    return np.asarray(current, dtype=np.int64)


def _robust_z(values: np.ndarray) -> np.ndarray:
    """Robust standardisation (median / MAD), safe for constant inputs."""
    values = np.asarray(values, dtype=np.float64)
    median = float(np.median(values)) if len(values) else 0.0
    mad = float(np.median(np.abs(values - median))) if len(values) else 0.0
    scale = mad if mad > 1e-12 else (float(np.std(values)) if len(values) else 1.0)
    if not np.isfinite(scale) or scale <= 1e-12:
        return np.zeros(len(values), dtype=np.float64)
    return (values - median) / scale


def label_events_sequential(
    times: np.ndarray,
    config: DetectorConfig | None = None,
    zcr: np.ndarray | None = None,
    amplitudes: np.ndarray | None = None,
) -> tuple[np.ndarray, dict]:
    """Label S1/S2 by local rhythm tracking instead of rigid alternation.

    Rigid alternation (every second event is S1) loses the phase permanently as
    soon as one event is spurious or missing, which was the dominant failure on
    real recordings. Here each step is scored against the two expected
    intervals: S1->S2 should be the shorter gap, S2->S1 the longer one. A
    same-label step is allowed at a fixed penalty, so a spurious or missed
    event is absorbed locally and the labelling recovers by itself.

    Two centroids are estimated from the gaps (short/long) and a two-state
    dynamic program picks the cheapest label sequence. Cost is
    ``((gap - expected) / expected)**2`` per step.

    Above ``config.prior_min_bpm`` the expected intervals are no longer taken
    from the gap clusters alone: the systolic-time-interval regression supplies
    the S1/S2 orientation (``gap_prior="orient"``) or both expectations
    (``gap_prior="model"``). ``gap_prior="cluster"`` keeps the legacy rule.
    """
    config = config or DetectorConfig()
    times = np.asarray(times, dtype=np.float64)
    n = len(times)
    empty_info = {
        "labeler": "sequential",
        "systole_est_s": float("nan"),
        "diastole_est_s": float("nan"),
        "label_separation": float("nan"),
        "gap_prior": config.gap_prior,
        "prior_active": False,
        "cycle_est_s": float("nan"),
        "systole_prior_s": float("nan"),
        "diastole_prior_s": float("nan"),
    }
    if n < 2:
        return np.full(n, S1, dtype=np.int8), empty_info

    gaps = np.diff(times)
    short, long = _two_gap_centres(gaps)
    separation = long / short if short > 0 else float("inf")

    # Physiological orientation prior (see the module docstring). The cycle
    # estimate is label-independent -- consecutive gaps span one cycle in
    # either S1/S2 order -- so deciding the orientation from it does not leak
    # the labels the DP is about to choose.
    cycle_s = _cycle_estimate(gaps)
    heart_rate = 60.0 / cycle_s if cycle_s > 0 else float("nan")
    prior_systole, prior_diastole = _systolic_prior(cycle_s, config)
    prior_active = bool(
        config.gap_prior in ("model", "orient")
        and np.isfinite(prior_systole)
        and np.isfinite(prior_diastole)
        and np.isfinite(heart_rate)
        and heart_rate > config.prior_min_bpm
    )

    base_info = {
        "labeler": "sequential",
        "systole_est_s": float("nan"),
        "diastole_est_s": float("nan"),
        "label_separation": separation,
        "gap_prior": config.gap_prior,
        "prior_active": prior_active,
        "cycle_est_s": cycle_s,
        "systole_prior_s": prior_systole,
        "diastole_prior_s": prior_diastole,
    }

    def fallback() -> tuple[np.ndarray, dict]:
        # The two phases are not separable by timing alone. Fall back to a
        # strict alternation, but still let the spectral/amplitude evidence
        # choose WHICH phase is S1.
        fallback_amplitude = (
            np.asarray(amplitudes, dtype=np.float64)
            if amplitudes is not None and len(amplitudes) == n
            else np.ones(n)
        )
        return label_events(
            times, fallback_amplitude, zcr=zcr, config=config
        ), {
            **base_info,
            "systole_est_s": short,
            "diastole_est_s": long,
            "labeler": "sequential-fallback",
        }

    clusters_usable = bool(np.isfinite(separation) and short > 0)
    if not prior_active and not clusters_usable:
        return fallback()

    if prior_active:
        if config.gap_prior == "model":
            expected_systole, expected_diastole = prior_systole, prior_diastole
        else:
            # The observed clusters keep the recording's own durations; the
            # regression only decides which cluster is the systole.
            if prior_systole >= prior_diastole:
                expected_systole, expected_diastole = long, short
            else:
                expected_systole, expected_diastole = short, long
        labeler_name = "sequential-prior"
    else:
        if separation < config.min_gap_separation:
            return fallback()
        expected_systole, expected_diastole = short, long
        labeler_name = "sequential"

    if not (expected_systole > 0 and expected_diastole > 0):
        base_info["prior_active"] = False
        return fallback()

    # Unary evidence: S1 is usually lower-frequency than S2, and often louder
    # at the auscultation site. Both are weak, recording-dependent cues, so they
    # are only added when their weights are non-zero, and always as a soft cost
    # that the interval model can outvote.
    unary = np.zeros((n, 2), dtype=np.float64)
    if config.evidence_weight_zcr > 0 and zcr is not None and len(zcr) == n:
        high_freq = _robust_z(zcr)
        unary[:, 0] += config.evidence_weight_zcr * high_freq
        unary[:, 1] -= config.evidence_weight_zcr * high_freq
    if (
        config.evidence_weight_amplitude > 0
        and amplitudes is not None
        and len(amplitudes) == n
    ):
        loud = _robust_z(amplitudes)
        unary[:, 0] -= config.evidence_weight_amplitude * loud
        unary[:, 1] += config.evidence_weight_amplitude * loud

    inf = float("inf")
    cost = np.full((n, 2), inf)
    back = np.zeros((n, 2), dtype=np.int8)
    cost[0] = (unary[0, 0], unary[0, 1])

    two_phases = expected_systole + expected_diastole

    def step(g: float, from_state: int, to_state: int) -> float:
        if from_state == 0 and to_state == 1:      # S1 -> S2 is systole
            return ((g - expected_systole) / expected_systole) ** 2
        if from_state == 1 and to_state == 0:      # S2 -> S1 is diastole
            return ((g - expected_diastole) / expected_diastole) ** 2
        # Same label twice means one beat was missed between them, so the gap
        # should span a whole cycle. Charging the gap (instead of a flat
        # penalty) is what stops the optimiser from "fixing" a single missed
        # event by inverting the phase of the entire recording.
        return ((g - two_phases) / two_phases) ** 2

    for i, g in enumerate(gaps):
        for to_state in (0, 1):
            best = inf
            best_from = 0
            for from_state in (0, 1):
                value = cost[i, from_state] + step(g, from_state, to_state)
                if value < best:
                    best = value
                    best_from = from_state
            cost[i + 1, to_state] = best + unary[i + 1, to_state]
            back[i + 1, to_state] = best_from

    labels = np.zeros(n, dtype=np.int8)
    state = int(np.argmin(cost[n - 1]))
    for i in range(n - 1, -1, -1):
        labels[i] = S1 if state == 0 else S2
        state = int(back[i, state])
    return labels, {
        **base_info,
        "labeler": labeler_name,
        "systole_est_s": expected_systole,
        "diastole_est_s": expected_diastole,
    }


def estimate_heart_rate(times: np.ndarray) -> float:
    """Heart rate from alternating S1/S2 spacing, or NaN if not estimable."""
    times = np.asarray(times, dtype=np.float64)
    if len(times) < 3:
        return float("nan")
    gaps = np.diff(times)
    pairs = gaps[: (len(gaps) // 2) * 2].reshape(-1, 2).sum(axis=1)
    if pairs.size == 0:
        return float("nan")
    rr = float(np.median(pairs))
    return 60.0 / rr if rr > 0 else float("nan")


def detect_s1s2(
    signal: np.ndarray, config: DetectorConfig | None = None
) -> DetectionResult:
    """Detect S1/S2 events in a single-channel PCG recording."""
    config = config or DetectorConfig()
    signal = np.asarray(signal, dtype=np.float64).reshape(-1)
    empty = DetectionResult(
        events=make_event_set(np.zeros(0), np.zeros(0)),
        info={"reason": "empty", "peaks": 0, "heart_rate_bpm": float("nan")},
    )
    if len(signal) < config.num_taps or not np.all(np.isfinite(signal)):
        return empty

    centered = signal - float(np.mean(signal))
    if float(np.std(centered)) < 1e-6:
        return empty

    taps = design_bandpass_fir(
        config.sample_rate,
        low_hz=config.band_low_hz,
        high_hz=config.band_high_hz,
        num_taps=config.num_taps,
    )
    filtered = filtfilt(centered, taps).astype(np.float64)

    envelope = _moving_rms(filtered, config.samples(config.envelope_ms))
    noise_floor = _block_median(envelope, config.samples(config.noise_block_s * 1000.0))
    adaptive = config.threshold_ratio * noise_floor
    floor_value = config.min_threshold_fraction * float(np.percentile(envelope, 99))
    threshold = np.maximum(adaptive, floor_value)

    peaks = _select_peaks(
        envelope,
        threshold,
        config.samples(config.refractory_ms),
        config.samples(config.prominence_window_ms),
        config.prominence_fraction,
    )
    if len(peaks) < config.min_events:
        return DetectionResult(
            events=make_event_set(np.zeros(0), np.zeros(0)),
            info={
                "reason": "too_few_peaks",
                "peaks": int(len(peaks)),
                "heart_rate_bpm": float("nan"),
            },
        )

    merge_enabled = config.merge_fraction > 0 or config.merge_min_gap_s > 0
    if merge_enabled and len(peaks) >= 3:
        presumptive_gaps = np.diff(peaks.astype(np.float64)) / float(config.sample_rate)
        short_gap, _ = _two_gap_centres(presumptive_gaps)
        if np.isfinite(short_gap) and short_gap > 0:
            # Never merge above the physiological floor: two heart sounds
            # cannot be closer than ~100 ms even at 180-200 bpm, and the
            # short-gap estimate itself is corrupted when spurious peaks are
            # present, so a fixed floor is more reliable than the estimate.
            merge_gap_s = max(
                config.merge_min_gap_s, config.merge_fraction * short_gap
            )
            peaks = _merge_close_peaks(
                peaks, envelope, merge_gap_s * float(config.sample_rate)
            )

    times = peaks.astype(np.float64) / float(config.sample_rate)
    amplitudes = envelope[peaks]
    zcr = _zero_crossing_hz(
        filtered, peaks, config.samples(30.0), config.sample_rate
    )
    if config.labeler == "sequential":
        labels, label_info = label_events_sequential(
            times, config=config, zcr=zcr, amplitudes=amplitudes
        )
    else:
        labels = label_events(times, amplitudes, zcr=zcr, config=config)
        label_info = {"labeler": "parity"}
    gaps = np.diff(times)

    median_systolic = _median_gap(gaps[0::2])
    median_diastolic = _median_gap(gaps[1::2])
    if np.isfinite(median_systolic) and np.isfinite(median_diastolic):
        mean_gap = 0.5 * (median_systolic + median_diastolic)
        spread = abs(median_systolic - median_diastolic) / max(mean_gap, 1e-9)
    else:
        spread = float("nan")

    heart_rate = estimate_heart_rate(times)
    info = {
        "reason": "ok",
        "peaks": int(len(peaks)),
        "heart_rate_bpm": heart_rate,
        "median_gap_s": float(np.median(gaps)) if gaps.size else float("nan"),
        "systole_s": median_systolic,
        "diastole_s": median_diastolic,
        "label_interval_spread": spread,
        # Reliability is defined physiologically, not from the detected gaps:
        # above ~120 bpm systole and diastole converge and a single-site PCG
        # cannot order S1/S2 from timing alone.
        "labeling_reliable": bool(
            np.isfinite(heart_rate) and heart_rate <= config.reliable_max_bpm
        ),
        **label_info,
        "threshold_mean": float(np.mean(threshold)),
        "envelope_p99": float(np.percentile(envelope, 99)),
        "duration_s": len(signal) / float(config.sample_rate),
    }
    return DetectionResult(events=make_event_set(times, labels), info=info)


def detect_file(path, config: DetectorConfig | None = None) -> DetectionResult:
    """Convenience wrapper that reads a PCM WAV and adapts the sample rate."""
    from hs_features import read_wav_mono

    signal, sample_rate = read_wav_mono(path)
    base = config or DetectorConfig()
    return detect_s1s2(signal, base.with_sample_rate(sample_rate))
