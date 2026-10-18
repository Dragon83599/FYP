"""Event-level evaluation for S1/S2 detection.

The detector output and the CirCor annotation are both reduced to a set of
timed events with a binary class (S1 or S2). Matching uses the +/- tolerance
required by ADR-008 (default 100 ms) and reports, per class:

- precision, recall and F1,
- the S1/S2 swap count and swap rate,
- the boundary error distribution of correctly matched events.

CirCor segment labels are ``1=S1``, ``2=systole``, ``3=S2``, ``4=diastole``.
Only labels 1 and 3 carry an event; this module maps them onto ``S1``/``S2``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

S1 = 1
S2 = 2

#: CirCor segment label -> event label.
SEGMENT_TO_EVENT = {1: S1, 3: S2}


@dataclass(frozen=True)
class EventSet:
    """Sorted event times in seconds together with their class labels."""

    times: np.ndarray
    labels: np.ndarray

    def __len__(self) -> int:
        return int(len(self.times))

    def count(self, label: int) -> int:
        return int(np.count_nonzero(self.labels == label))


EMPTY = EventSet(times=np.zeros(0, dtype=np.float64), labels=np.zeros(0, dtype=np.int8))


def make_event_set(times, labels) -> EventSet:
    """Validate, sort and freeze an event set."""
    times = np.asarray(times, dtype=np.float64).reshape(-1)
    labels = np.asarray(labels, dtype=np.int8).reshape(-1)
    if len(times) != len(labels):
        raise ValueError("times and labels must have the same length")
    if len(times) == 0:
        return EMPTY
    if not np.all(np.isfinite(times)):
        raise ValueError("event times must be finite")
    invalid = set(np.unique(labels).tolist()) - {S1, S2}
    if invalid:
        raise ValueError(f"unknown event labels: {sorted(invalid)}")
    order = np.argsort(times, kind="mergesort")
    return EventSet(times=times[order], labels=labels[order])


def segments_to_events(segments) -> EventSet:
    """Convert CirCor-style ``(start, end, label)`` rows into events.

    The event time is the centre of the labelled sound segment, which is the
    midpoint between the annotation boundaries.
    """
    segments = np.asarray(segments, dtype=np.float64)
    if segments.size == 0:
        return EMPTY
    times: list[float] = []
    labels: list[int] = []
    for start_seconds, end_seconds, raw_label in segments:
        label = SEGMENT_TO_EVENT.get(int(raw_label))
        if label is None or end_seconds <= start_seconds:
            continue
        times.append((float(start_seconds) + float(end_seconds)) / 2.0)
        labels.append(label)
    return make_event_set(times, labels)


def _candidate_pairs(ref_times: np.ndarray, hyp_times: np.ndarray, tolerance_s: float):
    """All (|dt|, ref_index, hyp_index) pairs inside the tolerance, sorted."""
    pairs: list[tuple[float, int, int]] = []
    if len(ref_times) == 0 or len(hyp_times) == 0:
        return pairs
    start = 0
    for ref_index, ref_time in enumerate(ref_times):
        while start < len(hyp_times) and hyp_times[start] < ref_time - tolerance_s:
            start += 1
        index = start
        while index < len(hyp_times) and hyp_times[index] <= ref_time + tolerance_s:
            pairs.append((abs(float(hyp_times[index]) - float(ref_time)), ref_index, index))
            index += 1
    pairs.sort(key=lambda item: (item[0], item[1], item[2]))
    return pairs


def match_events(ref: EventSet, hyp: EventSet, tolerance_s: float) -> list[tuple[int, int, float]]:
    """One-to-one greedy matching by ascending absolute time difference.

    Returns a list of ``(ref_index, hyp_index, signed_dt)``. Greedy matching is
    deterministic and adequate for S1/S2 because true events are separated by
    far more than the tolerance.
    """
    pairs = _candidate_pairs(ref.times, hyp.times, tolerance_s)
    used_ref: set[int] = set()
    used_hyp: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for _, ref_index, hyp_index in pairs:
        if ref_index in used_ref or hyp_index in used_hyp:
            continue
        used_ref.add(ref_index)
        used_hyp.add(hyp_index)
        dt = float(hyp.times[hyp_index] - ref.times[ref_index])
        matches.append((ref_index, hyp_index, dt))
    matches.sort(key=lambda item: item[0])
    return matches


def _safe_div(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else float("nan")


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    denominator = precision + recall
    f1 = 2.0 * precision * recall / denominator if denominator and np.isfinite(denominator) else 0.0
    return {
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "precision": precision,
        "recall": recall,
        "f1": float(f1),
    }


def evaluate(ref: EventSet, hyp: EventSet, tolerance_s: float = 0.1) -> dict:
    """Event-level S1/S2 metrics for one recording or one patient."""
    if tolerance_s <= 0:
        raise ValueError("tolerance must be positive")
    matches = match_events(ref, hyp, tolerance_s)

    same: list[float] = []
    swapped = 0
    matched_ref: set[int] = set()
    matched_hyp: set[int] = set()
    for ref_index, hyp_index, dt in matches:
        matched_ref.add(ref_index)
        matched_hyp.add(hyp_index)
        if ref.labels[ref_index] == hyp.labels[hyp_index]:
            same.append(abs(dt))
        else:
            swapped += 1

    per_class: dict[str, dict] = {}
    for label, name in ((S1, "s1"), (S2, "s2")):
        tp = sum(
            1
            for ref_index, hyp_index, _ in matches
            if ref.labels[ref_index] == label and hyp.labels[hyp_index] == label
        )
        fn = ref.count(label) - tp
        fp = hyp.count(label) - tp
        per_class[name] = _prf(tp, fp, fn)

    boundary = np.asarray(same, dtype=np.float64)
    f1_values = [per_class["s1"]["f1"], per_class["s2"]["f1"]]
    finite_f1 = [value for value in f1_values if np.isfinite(value)]
    n_matches = len(matches)
    return {
        "tolerance_s": float(tolerance_s),
        "reference": {"n": len(ref), "s1": ref.count(S1), "s2": ref.count(S2)},
        "detected": {"n": len(hyp), "s1": hyp.count(S1), "s2": hyp.count(S2)},
        "matches": {
            "total": n_matches,
            "same_class": len(same),
            "swapped": int(swapped),
            "swap_rate": _safe_div(swapped, n_matches),
        },
        "per_class": per_class,
        "f1_macro": float(np.mean(finite_f1)) if finite_f1 else float("nan"),
        "boundary_error_s": describe_errors(boundary),
        "boundary_errors": boundary.tolist(),
    }


def describe_errors(values) -> dict:
    """Summarize a flat sequence of boundary errors in seconds."""
    array = np.asarray(list(values), dtype=np.float64).reshape(-1)
    if array.size == 0:
        return {
            "n": 0,
            "mean": float("nan"),
            "median": float("nan"),
            "p95": float("nan"),
            "max": float("nan"),
        }
    return {
        "n": int(array.size),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
    }


def counts(report: dict) -> dict:
    """Extract additive TP/FP/FN counts so reports can be pooled."""
    return {
        "tp_s1": report["per_class"]["s1"]["tp"],
        "fp_s1": report["per_class"]["s1"]["fp"],
        "fn_s1": report["per_class"]["s1"]["fn"],
        "tp_s2": report["per_class"]["s2"]["tp"],
        "fp_s2": report["per_class"]["s2"]["fp"],
        "fn_s2": report["per_class"]["s2"]["fn"],
        "swapped": report["matches"]["swapped"],
        "matches": report["matches"]["total"],
        "n_reference": report["reference"]["n"],
        "n_detected": report["detected"]["n"],
    }


#: Additive counters produced by :func:`counts`, in a fixed order.
COUNT_KEYS = (
    "tp_s1",
    "fp_s1",
    "fn_s1",
    "tp_s2",
    "fp_s2",
    "fn_s2",
    "swapped",
    "matches",
    "n_reference",
    "n_detected",
)


def add_counts(left: dict, right: dict) -> dict:
    """Sum two counter dicts, ignoring non-counter keys such as ``patient_id``."""
    return {
        key: int(left.get(key, 0)) + int(right.get(key, 0)) for key in COUNT_KEYS
    }


def metrics_from_counts(total: dict) -> dict:
    """Recompute metrics from pooled counts (same definitions as evaluate)."""
    per_class = {
        "s1": _prf(total.get("tp_s1", 0), total.get("fp_s1", 0), total.get("fn_s1", 0)),
        "s2": _prf(total.get("tp_s2", 0), total.get("fp_s2", 0), total.get("fn_s2", 0)),
    }
    f1_values = [per_class["s1"]["f1"], per_class["s2"]["f1"]]
    finite_f1 = [value for value in f1_values if np.isfinite(value)]
    matches = total.get("matches", 0)
    return {
        "per_class": per_class,
        "f1_macro": float(np.mean(finite_f1)) if finite_f1 else float("nan"),
        "swap_rate": _safe_div(total.get("swapped", 0), matches),
        "swapped": int(total.get("swapped", 0)),
        "matches": int(matches),
        "n_reference": int(total.get("n_reference", 0)),
        "n_detected": int(total.get("n_detected", 0)),
    }


def summarize(per_patient: list[dict], boundary_errors: list[list[float]] | None = None) -> dict:
    """Macro mean/std across patients plus pooled counts.

    ``per_patient`` entries must be dicts produced by :func:`counts` with a
    ``"patient_id"`` key. Macro averaging over patients prevents a single long
    recording from dominating the reported F1.
    """
    flat_errors = (
        [value for row in boundary_errors for value in row] if boundary_errors else []
    )
    if not per_patient:
        summary = {"patients": 0, "macro": {}, "pooled": {}}
        if flat_errors:
            summary["pooled"]["boundary_error_s"] = describe_errors(flat_errors)
        return summary
    total: dict = {}
    f1_s1: list[float] = []
    f1_s2: list[float] = []
    f1_macro: list[float] = []
    swap_rates: list[float] = []
    for row in per_patient:
        total = add_counts(total, row)
        metrics = metrics_from_counts(row)
        if np.isfinite(metrics["per_class"]["s1"]["f1"]):
            f1_s1.append(metrics["per_class"]["s1"]["f1"])
        if np.isfinite(metrics["per_class"]["s2"]["f1"]):
            f1_s2.append(metrics["per_class"]["s2"]["f1"])
        if np.isfinite(metrics["f1_macro"]):
            f1_macro.append(metrics["f1_macro"])
        if np.isfinite(metrics["swap_rate"]):
            swap_rates.append(metrics["swap_rate"])

    def mean_std(values: list[float]) -> dict:
        if not values:
            return {"mean": float("nan"), "std": float("nan"), "n": 0}
        array = np.asarray(values, dtype=np.float64)
        return {
            "mean": float(np.mean(array)),
            "std": float(np.std(array, ddof=1)) if len(array) > 1 else 0.0,
            "n": int(len(array)),
        }

    summary = {
        "patients": len(per_patient),
        "macro": {
            "f1_s1": mean_std(f1_s1),
            "f1_s2": mean_std(f1_s2),
            "f1_macro": mean_std(f1_macro),
            "swap_rate": mean_std(swap_rates),
        },
        "pooled": metrics_from_counts(total),
    }
    if flat_errors:
        summary["pooled"]["boundary_error_s"] = describe_errors(flat_errors)
    return summary
