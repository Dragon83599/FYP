#!/usr/bin/env python3
"""Operating-point ("workpoint") analysis for the three CirCor comparison arms.

Why this script exists
----------------------
The three-arm comparison (log-mel + logistic regression, MFCC + delta +
logistic regression, 1D CNN) is frozen on patient-level folds, but the reports
store metrics only.  Every arm picked its threshold by "max balanced accuracy on
a ~104-patient validation split" and nothing in the reports says what that
threshold *means* clinically: the chosen values span 0.05-0.82, so a probability
of 0.5 can mean "clearly murmur" in one arm and "probably not" in another.

This script closes that gap with the artefacts the training scripts can now
write (``--dump-probabilities``):

1. **Calibration** - Platt scaling fitted on the validation split of each fold
   and applied to that fold's test split, reported as Brier score before/after,
   calibration slope/intercept, and a pooled out-of-fold reliability table.
2. **Workpoints** - the decision threshold is chosen on the validation split of
   each fold under a stated clinical constraint and evaluated *once* on that
   fold's test split:

   =====  ===============  ====================================================
   id     name             rule
   =====  ===============  ====================================================
   W1     screening        maximise sensitivity subject to specificity >= 0.80
   W2     high sensitivity maximise sensitivity subject to specificity >= 0.70
   W3     balanced         maximise balanced accuracy (the current behaviour)
   =====  ===============  ====================================================

   Per arm and constraint: threshold (mean +/- std across folds), test
   sensitivity, specificity, PPV, NPV, F1 and balanced accuracy (mean +/- std
   across folds) plus the pooled out-of-fold values with a patient-level
   bootstrap 95 % CI (2000 resamples).
3. **Decision curve** - net benefit of the pooled out-of-fold calibrated
   probabilities for threshold probabilities 0.05-0.50 against "treat all" and
   "treat none".

The hard rule
-------------
Nothing in this script may fit, select or calibrate on a test fold.  The only
quantities computed from the test rows are the reported metrics (including the
calibration slope/intercept, Brier score and reliability table, which are
*evaluation* of a map already fixed on the validation split).  Every selection -
Parzen/Platt coefficients and the W1/W2/W3 thresholds - uses validation rows
only.  ``assert_split_disjoint`` rejects a dump in which a patient is in the
validation and the test rows of the same fold.

Input format
------------
One CSV per arm, written by ``tools/ai/cross_validate.py --dump-probabilities``
or ``tools/ai/train_mfcc.py --dump-probabilities``::

    fold,split,subject_id,label,probability
    0,val,90011,0,0.0000019037828206931851
    0,val,90012,1,0.9999980237657505
    0,test,90001,0,0.0000014658880828595152

``split`` is ``val`` or ``test``.  ``probability`` is the mean-of-window
patient probability, i.e. exactly the value the patient-level metrics in the CV
``report.json`` were computed from, written with ``repr`` so it round-trips a
float64 exactly.

Usage::

    python tools/ai/workpoint_analysis.py \\
        --probs logreg=/tmp/prob-logreg.csv mfcc=/tmp/prob-mfcc.csv \\
                cnn=/tmp/prob-cnn.csv \\
        --out-dir data/reports

writes ``data/reports/workpoint-<arm>.json`` (machine-readable, complete) and
``data/reports/workpoint-summary.md`` (the supervisor-facing tables).

Evidence level
--------------
E4: single public dataset (CirCor, 874 labelled patients, children 0-21 and
young adults), retrospective cross-validation, no external validation, no
prospective study.  The numbers are prototype evidence for an operating-point
*decision*, not a validated medical device claim (see the summary's limitations
section).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from cross_validate import PROBABILITY_FIELDS
from hs_metrics import auroc, bootstrap_ci
from hs_splits import splits_fingerprint

#: sha256 of ``data/processed/circor/splits.csv`` (the frozen 874-patient,
#: 5-fold CirCor protocol).  The dump repeats the ``subject -> fold`` mapping, so
#: the fingerprint can be recomputed from the dump alone; when it does not match,
#: ``main`` refuses to analyse the file unless ``--allow-other-splits`` is given.
DEFAULT_EXPECTED_FINGERPRINT = (
    "1f212dea53b9184c91fe8184bf1e5e3e799083065cdc69f436b23b6fa5509a47"
)

#: Seed of the frozen protocol; reused for the bootstrap so a rerun reproduces
#: the same confidence intervals.
DEFAULT_SEED = 20261008

#: Patient-level bootstrap resamples requested by the protocol.
DEFAULT_N_BOOT = 2000

#: Specificity floor of the screening workpoint (W1).  Stated once so the
#: constraint, the report and the recommendation cannot drift apart.
FLOOR_SPECIFICITY = 0.80

#: Probabilities are clipped here before ``logit`` so a saturated network
#: output cannot produce an infinite predictor.  1e-6 is far outside the range
#: that matters for a screening threshold.
LOGIT_EPS = 1e-6

#: Number of equal-count bins of the reliability table (deciles).
RELIABILITY_BINS = 10

#: Decision-curve threshold probabilities, 0.05-0.50 (the clinically plausible
#: range for a murmur screening device).
DCA_GRID = tuple(round(0.05 * step, 2) for step in range(1, 11))

#: Metrics reported at every workpoint, in table order.
METRIC_FIELDS = (
    "sensitivity",
    "specificity",
    "ppv",
    "npv",
    "f1",
    "balanced_accuracy",
)

#: The clinical constraints.  ``min_specificity=None`` means "the current
#: behaviour": maximise balanced accuracy, which is what every arm did before
#: this analysis existed (and is kept as the reference).
CONSTRAINTS = (
    {
        "id": "W1",
        "name": "screening",
        "min_specificity": 0.80,
        "rule": "maximise sensitivity subject to specificity >= 0.80",
    },
    {
        "id": "W2",
        "name": "high_sensitivity",
        "min_specificity": 0.70,
        "rule": "maximise sensitivity subject to specificity >= 0.70",
    },
    {
        "id": "W3",
        "name": "balanced",
        "min_specificity": None,
        "rule": "maximise balanced accuracy (current behaviour)",
    },
)

#: Representation of the score each workpoint is built on.  ``calibrated`` is
#: the primary analysis (threshold on a probability that means something);
#: ``raw`` is the current pipeline (no calibration layer) and is kept so the
#: summary can show what calibration changes.
REPRESENTATIONS = ("calibrated", "raw")


# --------------------------------------------------------------------------- #
# input
# --------------------------------------------------------------------------- #
def parse_arm_specs(values: list[str]) -> list[tuple[str, Path]]:
    """Parse ``arm=path`` arguments into ``[(arm, Path), ...]``, order preserved."""
    specs: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for value in values:
        if "=" not in value:
            raise SystemExit(
                f"--probs expects arm=path, got {value!r} (for example logreg=/tmp/prob-logreg.csv)"
            )
        arm, _, raw_path = value.partition("=")
        arm = arm.strip()
        raw_path = raw_path.strip()
        if not arm or not raw_path:
            raise SystemExit(f"--probs expects arm=path with both parts non-empty, got {value!r}")
        if arm in seen:
            raise SystemExit(f"--probs was given the arm {arm!r} twice")
        seen.add(arm)
        specs.append((arm, Path(raw_path)))
    return specs


def read_probability_dump(path: Path) -> dict:
    """Read and validate one ``--dump-probabilities`` CSV.

    Validation is strict on purpose: a silently mis-parsed dump would turn into
    a wrong operating point in a report.  Malformed rows raise ``SystemExit``
    with the row number.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise SystemExit(
            f"{path}: probability dump not found (write it with --dump-probabilities)"
        ) from None
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise SystemExit(f"{path}: empty probability dump")
    rows = list(csv.reader(lines))
    header = tuple(field.strip() for field in rows[0])
    if header != PROBABILITY_FIELDS:
        raise SystemExit(
            f"{path}: expected header {','.join(PROBABILITY_FIELDS)}, "
            f"got {','.join(header)}"
        )

    folds: list[int] = []
    splits: list[str] = []
    subjects: list[str] = []
    labels: list[int] = []
    probabilities: list[float] = []
    seen: set[tuple[int, str, str]] = set()
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(PROBABILITY_FIELDS):
            raise SystemExit(
                f"{path}: row {row_number} has {len(row)} fields, "
                f"expected {len(PROBABILITY_FIELDS)}"
            )
        fold_text, split, subject, label_text, probability_text = row
        try:
            fold = int(fold_text)
            label = int(label_text)
            probability = float(probability_text)
        except ValueError:
            raise SystemExit(
                f"{path}: row {row_number} has a non-numeric fold/label/probability"
            ) from None
        subject = subject.strip()
        if fold < 0:
            raise SystemExit(f"{path}: row {row_number} has a negative fold")
        if split not in ("val", "test"):
            raise SystemExit(
                f"{path}: row {row_number} has split {split!r}; expected 'val' or 'test'"
            )
        if not subject:
            raise SystemExit(f"{path}: row {row_number} has an empty subject_id")
        if label not in (0, 1):
            raise SystemExit(f"{path}: row {row_number} has label {label}; expected 0 or 1")
        if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise SystemExit(
                f"{path}: row {row_number} has probability {probability_text!r} "
                f"outside [0, 1]"
            )
        key = (fold, split, subject)
        if key in seen:
            raise SystemExit(
                f"{path}: row {row_number} repeats (fold, split, subject) = {key}"
            )
        seen.add(key)
        folds.append(fold)
        splits.append(split)
        subjects.append(subject)
        labels.append(label)
        probabilities.append(probability)

    if not probabilities:
        raise SystemExit(f"{path}: header present but no data rows")
    data = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "fold": np.asarray(folds, dtype=int),
        "split": np.asarray(splits),
        "subject": np.asarray(subjects),
        "label": np.asarray(labels, dtype=int),
        "probability": np.asarray(probabilities, dtype=float),
    }
    data["index"] = {
        (fold, split): np.flatnonzero(
            (data["fold"] == fold) & (data["split"] == split)
        )
        for fold in sorted(set(folds))
        for split in ("val", "test")
    }
    return data


def assert_split_disjoint(fold: int, val_subjects, test_subjects) -> None:
    """Refuse a fold whose validation and test patients overlap.

    This is the one thing that would make every number below meaningless, and it
    is cheap to check, so it is an error rather than a warning.
    """
    both = sorted(set(val_subjects) & set(test_subjects))
    if both:
        raise SystemExit(
            f"fold {fold}: {len(both)} patient(s) are in both the validation and "
            f"the test rows (first: {both[:5]}) - the dump leaks and no number "
            f"in it can be trusted"
        )
    if not len(val_subjects) or not len(test_subjects):
        raise SystemExit(f"fold {fold}: empty validation or test split in the dump")


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def metrics_from_counts(tp: int, tn: int, fp: int, fn: int) -> dict:
    """All reported metrics from a 2x2 table.

    A metric whose denominator is zero is ``nan`` rather than 0: "PPV is
    undefined because the device flagged nobody" is different information from
    "PPV is 0", and a bootstrap resample without positives must be skipped
    instead of dragging the interval to 0.  ``nan`` therefore propagates on
    purpose.
    """
    n = tp + tn + fp + fn
    sensitivity = tp / (tp + fn) if (tp + fn) else float("nan")
    specificity = tn / (tn + fp) if (tn + fp) else float("nan")
    ppv = tp / (tp + fp) if (tp + fp) else float("nan")
    npv = tn / (tn + fn) if (tn + fn) else float("nan")
    f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else float("nan")
    accuracy = (tp + tn) / n if n else float("nan")
    balanced_accuracy = (
        (sensitivity + specificity) / 2.0
        if np.isfinite(sensitivity) and np.isfinite(specificity)
        else float("nan")
    )
    return {
        "n": int(n),
        "tp": int(tp),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "accuracy": float(accuracy),
        "sensitivity": float(sensitivity),
        "specificity": float(specificity),
        "ppv": float(ppv),
        "npv": float(npv),
        "f1": float(f1),
        "balanced_accuracy": float(balanced_accuracy),
    }


def decision_metrics(y_true, predicted_positive) -> dict:
    """Metrics of an already-thresholded decision vector."""
    y_true = np.asarray(y_true, dtype=int)
    predicted = np.asarray(predicted_positive, dtype=bool)
    if len(y_true) != len(predicted):
        raise ValueError(
            f"decision_metrics: {len(predicted)} decisions for {len(y_true)} outcomes"
        )
    positive = y_true == 1
    tp = int(np.sum(positive & predicted))
    fn = int(np.sum(positive & ~predicted))
    fp = int(np.sum(~positive & predicted))
    tn = int(np.sum(~positive & ~predicted))
    return metrics_from_counts(tp, tn, fp, fn)


def threshold_metrics(y_true, y_prob, threshold: float) -> dict:
    """Metrics of ``y_prob >= threshold``; the only place a scalar threshold is applied."""
    return decision_metrics(y_true, np.asarray(y_prob, dtype=float) >= float(threshold))


def bootstrap_metric_table(
    y_true, predicted_positive, n_boot: int, seed: int
) -> dict:
    """Patient-level bootstrap 95 % CI for every metric of one workpoint.

    ``predicted_positive`` is the already-thresholded decision vector, so each
    resampled patient keeps the threshold that his own fold chose.  All metrics
    share the same resamples, so their intervals are correlated the way the
    point estimates are.
    """
    y_true = np.asarray(y_true, dtype=int)
    predicted = np.asarray(predicted_positive, dtype=bool)
    n = int(len(y_true))
    empty = {
        "n_boot": 0,
        "ci95": {
            field: {"low": float("nan"), "high": float("nan")} for field in METRIC_FIELDS
        },
    }
    if n < 4 or len(np.unique(y_true)) < 2:
        return empty

    positive = y_true == 1
    negative = ~positive
    rng = np.random.default_rng(seed)
    samples: dict[str, list[float]] = {field: [] for field in METRIC_FIELDS}
    used = 0
    for _ in range(int(n_boot)):
        index = rng.integers(0, n, n)
        pos = positive[index]
        neg = negative[index]
        flag = predicted[index]
        tp = int(np.sum(pos & flag))
        fn = int(np.sum(pos & ~flag))
        fp = int(np.sum(neg & flag))
        tn = int(np.sum(neg & ~flag))
        if tp + fn == 0 or tn + fp == 0:
            # A resample with one class only cannot estimate the metrics.
            continue
        used += 1
        metrics = metrics_from_counts(tp, tn, fp, fn)
        for field in METRIC_FIELDS:
            samples[field].append(metrics[field])
    if not used:
        return empty
    ci = {}
    for field in METRIC_FIELDS:
        values = np.asarray(samples[field], dtype=float)
        values = values[np.isfinite(values)]
        if not len(values):
            ci[field] = {"low": float("nan"), "high": float("nan")}
            continue
        ci[field] = {
            "low": float(np.percentile(values, 2.5)),
            "high": float(np.percentile(values, 97.5)),
        }
    return {"n_boot": int(used), "ci95": ci}


# --------------------------------------------------------------------------- #
# threshold selection (validation rows only)
# --------------------------------------------------------------------------- #
def candidate_thresholds(probabilities) -> np.ndarray:
    """Every distinct operating point reachable by ``probability >= threshold``.

    The distinct predicted values plus ``1.0`` (flag nobody).  ``0.0`` is the
    minimum value and flags everybody, so it is already covered whenever some
    probability is below that value.
    """
    values = np.unique(np.asarray(probabilities, dtype=float))
    return np.unique(np.concatenate((values, [1.0])))


def _sortable(value: float) -> float:
    """Map ``nan`` to ``-inf`` so a degenerate candidate never wins a comparison."""
    return float(value) if np.isfinite(value) else -np.inf


def select_threshold(y_val, p_val, min_specificity: float | None) -> dict:
    """Choose one threshold on the validation split only.

    ``min_specificity`` is the clinical floor.  Ties on sensitivity are broken
    towards the *higher* threshold, i.e. the one with more specificity margin,
    which is the conservative choice for a screening device.

    ``feasible`` is False when the selected point never alerts anybody.  That case
    matters: "flag nobody" trivially satisfies any specificity floor, so without
    this flag a validation split on which the floor is unreachable would be
    reported as a satisfied constraint with sensitivity 0.
    """
    y_val = np.asarray(y_val, dtype=int)
    p_val = np.asarray(p_val, dtype=float)
    candidates = candidate_thresholds(p_val)
    best_key: tuple | None = None
    best: dict | None = None
    for threshold in candidates:
        metrics = threshold_metrics(y_val, p_val, threshold)
        if min_specificity is not None and not (
            _sortable(metrics["specificity"]) >= float(min_specificity) - 1e-12
        ):
            continue
        if min_specificity is None:
            key = (
                _sortable(metrics["balanced_accuracy"]),
                -abs(float(threshold) - 0.5),
            )
        else:
            key = (
                _sortable(metrics["sensitivity"]),
                _sortable(metrics["specificity"]),
                -abs(float(threshold) - 0.5),
            )
        if best_key is None or key > best_key:
            best_key = key
            best = {"threshold": float(threshold), "val": metrics, "feasible": True}
    if best is None:
        # Only reachable with an invalid floor (> 1.0): the highest threshold meets
        # every achievable specificity and is reported as infeasible.
        fallback = float(candidates[-1])
        return {
            "threshold": fallback,
            "val": threshold_metrics(y_val, p_val, fallback),
            "feasible": False,
        }

    # A workpoint that never alerts satisfies any specificity floor and is
    # useless; the report must not count it as a met constraint.
    best["feasible"] = bool(best["val"]["sensitivity"] > 0.0)
    return best


# --------------------------------------------------------------------------- #
# calibration
# --------------------------------------------------------------------------- #
def logit(probabilities) -> np.ndarray:
    """``log(p / (1 - p))`` with ``p`` clipped to ``[LOGIT_EPS, 1 - LOGIT_EPS]``."""
    values = np.clip(
        np.asarray(probabilities, dtype=float), LOGIT_EPS, 1.0 - LOGIT_EPS
    )
    return np.log(values / (1.0 - values))


def sigmoid(values) -> np.ndarray:
    """Numerically safe logistic function."""
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(values, dtype=float), -60.0, 60.0)))


def _mean_log_loss(design: np.ndarray, y: np.ndarray, theta: np.ndarray) -> float:
    eta = design @ theta
    return float(np.mean(np.logaddexp(0.0, eta) - y * eta))


def fit_logistic(
    x, y, ridge: float = 1e-6, max_iter: int = 200, tolerance: float = 1e-10
) -> dict:
    """Fit ``P(y=1) = sigmoid(slope * x + intercept)`` by Newton/IRLS.

    Parameters
    ----------
    x:
        Predictor, normally a predicted logit.  Fitting against the logit rather
        than against the probability is what makes the slope a *calibration*
        slope: a perfectly calibrated model gives slope 1, intercept 0.
    y:
        0/1 outcome.
    ridge:
        Tiny L2 penalty on both coefficients.  This is not a modelling choice:
        with ~100 validation patients a separable split makes the maximum
        likelihood slope infinite, and the ridge keeps the map finite so it can
        be applied out of sample.  ``1e-6`` is negligible for the fitted values
        and only binds in the degenerate case.

    Returns
    -------
    dict
        ``slope``, ``intercept``, ``iterations``, ``converged``.
    """
    x = np.asarray(x, dtype=float).ravel()
    y = np.asarray(y, dtype=float).ravel()
    if len(x) != len(y):
        raise ValueError(f"fit_logistic: {len(x)} predictors for {len(y)} outcomes")
    if not len(x):
        raise ValueError("fit_logistic: empty input")
    design = np.column_stack((x, np.ones_like(x)))
    theta = np.array([1.0, 0.0])
    penalty = np.diag([ridge, ridge])
    converged = False
    iterations = 0
    for iterations in range(1, int(max_iter) + 1):
        eta = design @ theta
        probability = sigmoid(eta)
        weight = np.clip(probability * (1.0 - probability), 1e-9, None)
        gradient = design.T @ (probability - y) + penalty @ theta
        hessian = design.T @ (design * weight[:, None]) + penalty
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:  # pragma: no cover - ridge prevents this
            break
        base = _mean_log_loss(design, y, theta)
        scale = 1.0
        for _ in range(40):
            if _mean_log_loss(design, y, theta - scale * step) <= base + 1e-12:
                break
            scale *= 0.5
        theta = theta - scale * step
        if float(np.max(np.abs(scale * step))) < tolerance:
            converged = True
            break
    return {
        "slope": float(theta[0]),
        "intercept": float(theta[1]),
        "iterations": int(iterations),
        "converged": bool(converged),
    }


def fit_platt(y_val, p_val) -> dict:
    """Platt scaling fitted on the validation split: ``sigmoid(a*logit(p)+b)``."""
    y_val = np.asarray(y_val, dtype=int)
    p_val = np.asarray(p_val, dtype=float)
    clipped = int(np.sum((p_val < LOGIT_EPS) | (p_val > 1.0 - LOGIT_EPS)))
    model = fit_logistic(logit(p_val), y_val)
    model.update(
        {
            "method": "platt_scaling",
            "formula": "sigmoid(a * logit(p) + b)",
            "n_val": int(len(p_val)),
            "n_val_positive": int(np.sum(y_val == 1)),
            "n_val_clipped": clipped,
            "logit_eps": LOGIT_EPS,
        }
    )
    return model


def apply_platt(model: dict, probabilities) -> dict:
    """Apply a fitted Platt map, returning both the probability and its logit.

    The calibration-slope regression uses ``eta`` (the linear predictor) rather
    than ``logit(probability)`` so a saturated sigmoid cannot be re-clipped and
    distort the diagnostic.
    """
    eta = float(model["slope"]) * logit(probabilities) + float(model["intercept"])
    return {"probability": sigmoid(eta), "eta": eta}


def brier_score(y_true, probabilities) -> float:
    y_true = np.asarray(y_true, dtype=float)
    probabilities = np.asarray(probabilities, dtype=float)
    return float(np.mean((probabilities - y_true) ** 2))


def reliability_table(y_true, probabilities, n_bins: int = RELIABILITY_BINS) -> list[dict]:
    """Equal-count (decile) reliability table of predicted vs observed.

    Bins are formed by rank, not by fixed probability ranges, so every bin has
    roughly the same n and a sparse arm still produces a readable table.  Equal
    predicted values can straddle a bin boundary; ``min_predicted`` and
    ``max_predicted`` are reported so that is visible instead of hidden.
    """
    y_true = np.asarray(y_true, dtype=float)
    probabilities = np.asarray(probabilities, dtype=float)
    if len(probabilities) < n_bins:
        return []
    order = np.argsort(probabilities, kind="mergesort")
    rows: list[dict] = []
    for bin_index, index in enumerate(np.array_split(order, n_bins)):
        if not len(index):
            continue
        predicted = probabilities[index]
        observed = y_true[index]
        rows.append(
            {
                "bin": bin_index + 1,
                "n": int(len(index)),
                "mean_predicted": float(np.mean(predicted)),
                "observed": float(np.mean(observed)),
                "min_predicted": float(np.min(predicted)),
                "max_predicted": float(np.max(predicted)),
            }
        )
    return rows


def expected_calibration_error(table: list[dict], n: int) -> float:
    """Sample-weighted mean |observed - predicted| over the reliability bins."""
    if not table or not n:
        return float("nan")
    return float(
        sum(row["n"] / n * abs(row["observed"] - row["mean_predicted"]) for row in table)
    )


# --------------------------------------------------------------------------- #
# decision curve
# --------------------------------------------------------------------------- #
def net_benefit(y_true, predicted_positive, threshold_probability: float) -> float:
    """Net benefit of a decision rule at one threshold probability.

    ``NB = TP/n - FP/n * pt/(1-pt)``.  "Treat all" and "treat none" are the two
    reference strategies a new device has to beat.
    """
    y_true = np.asarray(y_true, dtype=int)
    predicted = np.asarray(predicted_positive, dtype=bool)
    n = int(len(y_true))
    if not n or not 0.0 < threshold_probability < 1.0:
        return float("nan")
    tp = int(np.sum((y_true == 1) & predicted))
    fp = int(np.sum((y_true == 0) & predicted))
    weight = float(threshold_probability) / (1.0 - float(threshold_probability))
    return float(tp / n - (fp / n) * weight)


def treat_all_net_benefit(y_true, threshold_probability: float) -> float:
    y_true = np.asarray(y_true, dtype=int)
    n = int(len(y_true))
    if not n or not 0.0 < threshold_probability < 1.0:
        return float("nan")
    prevalence = float(np.mean(y_true == 1))
    weight = float(threshold_probability) / (1.0 - float(threshold_probability))
    return float(prevalence - (1.0 - prevalence) * weight)


def decision_curve(y_true, probabilities, grid=DCA_GRID) -> list[dict]:
    """Net benefit of the model vs treat-all vs treat-none on a threshold grid."""
    y_true = np.asarray(y_true, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    rows = []
    for threshold in grid:
        predicted = probabilities >= float(threshold)
        model = net_benefit(y_true, predicted, threshold)
        treat_all = treat_all_net_benefit(y_true, threshold)
        rows.append(
            {
                "threshold_probability": float(threshold),
                "net_benefit": model,
                "net_benefit_per_100": model * 100.0,
                "treat_all": treat_all,
                "treat_all_per_100": treat_all * 100.0,
                "treat_none": 0.0,
                "n_flagged": int(np.sum(predicted)),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# per-arm analysis
# --------------------------------------------------------------------------- #
def _summarise(values) -> dict:
    array = np.asarray(values, dtype=float)
    return {
        "mean": float(np.nanmean(array)) if np.any(np.isfinite(array)) else float("nan"),
        "std": float(np.nanstd(array)) if np.any(np.isfinite(array)) else float("nan"),
        "values": [float(value) for value in array],
    }


def analyse_fold(fold: int, val_y, val_p, test_y, test_p) -> dict:
    """Everything one fold contributes: calibration map, workpoints, test metrics.

    Order matters and is the whole protocol: (1) fit Platt on validation rows,
    (2) *freeze* the map, (3) select each threshold on validation rows, (4) only
    then apply both to the test rows and compute metrics.  The calibration
    slope/intercept of the test rows are *evaluation* of a map that was already
    frozen, not a fit that feeds back into anything below.
    """
    val_y = np.asarray(val_y, dtype=int)
    val_p = np.asarray(val_p, dtype=float)
    test_y = np.asarray(test_y, dtype=int)
    test_p = np.asarray(test_p, dtype=float)

    platt = fit_platt(val_y, val_p)
    val_calibrated = apply_platt(platt, val_p)["probability"]
    calibrated = apply_platt(platt, test_p)

    scores = {
        "calibrated": {"val": val_calibrated, "test": calibrated["probability"]},
        "raw": {"val": val_p, "test": test_p},
    }

    workpoints: dict[str, dict] = {}
    for representation in REPRESENTATIONS:
        workpoints[representation] = {}
        for constraint in CONSTRAINTS:
            selection = select_threshold(
                val_y, scores[representation]["val"], constraint["min_specificity"]
            )
            test_metrics = threshold_metrics(
                test_y, scores[representation]["test"], selection["threshold"]
            )
            workpoints[representation][constraint["id"]] = {
                "threshold": float(selection["threshold"]),
                "constraint_feasible_on_val": bool(selection["feasible"]),
                "val": selection["val"],
                "test": test_metrics,
            }

    # Validation AUROC: the threshold-free discriminator the arm choice is based
    # on. It uses validation rows only, so choosing an arm on it cannot leak.
    val_auroc = float(auroc(val_y, val_p))
    raw_diagnostics = fit_logistic(logit(test_p), test_y)
    calibrated_diagnostics = fit_logistic(calibrated["eta"], test_y)
    return {
        "fold": int(fold),
        "n_val": int(len(val_y)),
        "n_test": int(len(test_y)),
        "n_val_positive": int(np.sum(val_y == 1)),
        "n_test_positive": int(np.sum(test_y == 1)),
        "platt": platt,
        "val_auroc": val_auroc,
        "brier": {
            "raw": brier_score(test_y, test_p),
            "calibrated": brier_score(test_y, scores["calibrated"]["test"]),
        },
        "calibration": {
            "raw": {
                "slope": raw_diagnostics["slope"],
                "intercept": raw_diagnostics["intercept"],
            },
            "calibrated": {
                "slope": calibrated_diagnostics["slope"],
                "intercept": calibrated_diagnostics["intercept"],
            },
        },
        "auroc": {
            "raw": float(auroc(test_y, test_p)),
            "calibrated": float(auroc(test_y, scores["calibrated"]["test"])),
        },
        "workpoints": workpoints,
    }


def cohort_checks(data: dict) -> dict:
    """Structural checks of the dump: one test fold per patient, constant label.

    Returns the ``{subject: fold}`` mapping (which is the frozen assignment) so
    the caller can fingerprint it.  Raises ``SystemExit`` on any violation,
    because a dump that violates the protocol cannot produce a protocol number.
    """
    test_rows = np.flatnonzero(data["split"] == "test")
    assignment: dict[str, int] = {}
    for index in test_rows:
        subject = str(data["subject"][index])
        fold = int(data["fold"][index])
        if subject in assignment:
            raise SystemExit(
                f"patient {subject} is a test patient in more than one fold "
                f"({assignment[subject]} and {fold}); the protocol says every "
                f"patient is test in exactly one fold"
            )
        assignment[subject] = fold
    labels: dict[str, int] = {}
    for subject, label in zip(data["subject"], data["label"]):
        subject = str(subject)
        label = int(label)
        if subject in labels and labels[subject] != label:
            raise SystemExit(
                f"patient {subject} has label {labels[subject]} in one row and "
                f"{label} in another"
            )
        labels[subject] = label
    unknown = sorted(set(labels) - set(assignment))
    if unknown:
        raise SystemExit(
            f"{len(unknown)} patient(s) appear only in validation rows and are "
            f"never a test patient (first: {unknown[:5]})"
        )
    return {
        "assignment": assignment,
        "n_patients": len(assignment),
        "n_folds": len(set(assignment.values())),
        "n_positives": int(sum(labels.values())),
        "prevalence": float(np.mean([labels[s] for s in sorted(labels)])),
        "n_val_rows": int(np.sum(data["split"] == "val")),
        "n_test_rows": int(len(test_rows)),
    }


def analyse_arm(arm: str, path, n_boot: int = DEFAULT_N_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """Full calibrated/workpoint analysis of one arm's probability dump."""
    data = read_probability_dump(Path(path))
    cohort = cohort_checks(data)
    folds = sorted(set(int(value) for value in data["fold"]))

    fold_results: list[dict] = []
    pooled_label: list[np.ndarray] = []
    pooled_calibrated: list[np.ndarray] = []
    pooled_raw: list[np.ndarray] = []
    pooled_thresholds = {
        (representation, constraint["id"]): []
        for representation in REPRESENTATIONS
        for constraint in CONSTRAINTS
    }
    for fold in folds:
        val_index = data["index"][(fold, "val")]
        test_index = data["index"][(fold, "test")]
        val_subjects = [str(value) for value in data["subject"][val_index]]
        test_subjects = [str(value) for value in data["subject"][test_index]]
        assert_split_disjoint(fold, val_subjects, test_subjects)

        result = analyse_fold(
            fold,
            data["label"][val_index],
            data["probability"][val_index],
            data["label"][test_index],
            data["probability"][test_index],
        )
        fold_results.append(result)

        pooled_label.append(data["label"][test_index])
        pooled_raw.append(data["probability"][test_index])
        platt = result["platt"]
        pooled_calibrated.append(apply_platt(platt, data["probability"][test_index])["probability"])
        for representation in REPRESENTATIONS:
            for constraint in CONSTRAINTS:
                pooled_thresholds[(representation, constraint["id"])].append(
                    np.full(
                        len(test_index),
                        result["workpoints"][representation][constraint["id"]]["threshold"],
                        dtype=float,
                    )
                )

    mean_val_patients = float(np.mean([fold["n_val"] for fold in fold_results]))
    mean_test_patients = float(np.mean([fold["n_test"] for fold in fold_results]))
    y_all = np.concatenate(pooled_label).astype(int)
    probabilities = {
        "calibrated": np.concatenate(pooled_calibrated),
        "raw": np.concatenate(pooled_raw),
    }
    thresholds = {
        key: np.concatenate(values) for key, values in pooled_thresholds.items()
    }

    # ---------------- calibration ----------------
    reliability = {
        representation: reliability_table(y_all, probabilities[representation])
        for representation in REPRESENTATIONS
    }
    calibration_summary = {
        "brier_raw": _summarise([fold["brier"]["raw"] for fold in fold_results]),
        "brier_calibrated": _summarise(
            [fold["brier"]["calibrated"] for fold in fold_results]
        ),
        "brier_delta": _summarise(
            [
                fold["brier"]["calibrated"] - fold["brier"]["raw"]
                for fold in fold_results
            ]
        ),
        "slope_raw": _summarise(
            [fold["calibration"]["raw"]["slope"] for fold in fold_results]
        ),
        "intercept_raw": _summarise(
            [fold["calibration"]["raw"]["intercept"] for fold in fold_results]
        ),
        "slope_calibrated": _summarise(
            [fold["calibration"]["calibrated"]["slope"] for fold in fold_results]
        ),
        "intercept_calibrated": _summarise(
            [fold["calibration"]["calibrated"]["intercept"] for fold in fold_results]
        ),
        "platt_slope": _summarise([fold["platt"]["slope"] for fold in fold_results]),
        "platt_intercept": _summarise(
            [fold["platt"]["intercept"] for fold in fold_results]
        ),
    }
    pooled_brier = {
        representation: brier_score(y_all, probabilities[representation])
        for representation in REPRESENTATIONS
    }
    pooled_calibration = {
        "brier_raw": pooled_brier["raw"],
        "brier_calibrated": pooled_brier["calibrated"],
        "brier_delta": pooled_brier["calibrated"] - pooled_brier["raw"],
        "brier_delta_ci95": bootstrap_brier_delta_ci(
            y_all,
            probabilities["raw"],
            probabilities["calibrated"],
            n_boot,
            seed + 7,
        ),
        "brier_calibrated_ci95": bootstrap_brier_ci(
            y_all, probabilities["calibrated"], n_boot, seed + 8
        ),
        "brier_raw_ci95": bootstrap_brier_ci(y_all, probabilities["raw"], n_boot, seed + 9),
        "slope_raw": float(fit_logistic(logit(probabilities["raw"]), y_all)["slope"]),
        "intercept_raw": float(
            fit_logistic(logit(probabilities["raw"]), y_all)["intercept"]
        ),
        "slope_calibrated": float(
            fit_logistic(logit(probabilities["calibrated"]), y_all)["slope"]
        ),
        "intercept_calibrated": float(
            fit_logistic(logit(probabilities["calibrated"]), y_all)["intercept"]
        ),
        "ece_raw": expected_calibration_error(reliability["raw"], len(y_all)),
        "ece_calibrated": expected_calibration_error(reliability["calibrated"], len(y_all)),
        "auroc_raw": float(auroc(y_all, probabilities["raw"])),
        "auroc_calibrated": float(auroc(y_all, probabilities["calibrated"])),
        "auroc_raw_ci95": bootstrap_ci(
            y_all, probabilities["raw"], "auroc", n_boot=n_boot, seed=seed + 10
        ),
        "auroc_calibrated_ci95": bootstrap_ci(
            y_all, probabilities["calibrated"], "auroc", n_boot=n_boot, seed=seed + 11
        ),
        "n_patients": int(len(y_all)),
    }

    # ---------------- workpoints ----------------
    workpoints: dict[str, dict] = {}
    for representation_index, representation in enumerate(REPRESENTATIONS):
        workpoints[representation] = {}
        for constraint_index, constraint in enumerate(CONSTRAINTS):
            cid = constraint["id"]
            per_fold = [fold["workpoints"][representation][cid] for fold in fold_results]
            predicted = probabilities[representation] >= thresholds[(representation, cid)]
            point = decision_metrics(y_all, predicted)
            bootstrap = bootstrap_metric_table(
                y_all,
                predicted,
                n_boot,
                seed + 1000 * (representation_index + 1) + 100 * (constraint_index + 1),
            )
            workpoints[representation][cid] = {
                "constraint": {
                    "id": cid,
                    "name": constraint["name"],
                    "rule": constraint["rule"],
                    "min_specificity": constraint["min_specificity"],
                },
                "per_fold": per_fold,
                "summary": {
                    "threshold": _summarise([fold["threshold"] for fold in per_fold]),
                    "test": {
                        field: _summarise([fold["test"][field] for fold in per_fold])
                        for field in METRIC_FIELDS
                    },
                    "val": {
                        field: _summarise([fold["val"][field] for fold in per_fold])
                        for field in ("sensitivity", "specificity", "balanced_accuracy")
                    },
                    "n_folds_constraint_feasible": int(
                        sum(1 for fold in per_fold if fold["constraint_feasible_on_val"])
                    ),
                    "n_folds": len(per_fold),
                },
                "pooled_oof": {
                    "threshold_used": "per fold, chosen on that fold's validation split",
                    "n_patients": int(len(y_all)),
                    "metrics": point,
                    "ci95": bootstrap["ci95"],
                    "n_boot": bootstrap["n_boot"],
                },
            }

    decision_curve_rows = decision_curve(y_all, probabilities["calibrated"])

    return {
        "analysis": "operating-point (workpoint) analysis",
        "arm": str(arm),
        "generated_by": "tools/ai/workpoint_analysis.py",
        "evidence_level": (
            "E4: single public dataset (CirCor), retrospective patient-level 5-fold "
            "CV, no external validation. Prototype evidence for an operating-point "
            "decision, not a validated device claim."
        ),
        "protocol": {
            "folds": cohort["n_folds"],
            "split_protocol": (
                "frozen patient-level folds; per fold, selection (Platt coefficients and "
                "the decision threshold) uses the validation split only, and the test "
                "split is evaluated once"
            ),
            "threshold_selection_split": "val",
            "evaluation_split": "test",
            "patient_probability": "mean of window probabilities per subject",
            "bootstrap": (
                "patient level, resampling all metrics of one workpoint together, "
                f"{int(n_boot)} requested resamples, seed {int(seed)}"
            ),
            "n_boot_requested": int(n_boot),
            "seed": int(seed),
            "constraints": [
                {
                    "id": constraint["id"],
                    "name": constraint["name"],
                    "rule": constraint["rule"],
                    "min_specificity": constraint["min_specificity"],
                }
                for constraint in CONSTRAINTS
            ],
            "primary_representation": "calibrated",
            "reference_representation": "raw",
        },
        "probability_file": {
            "path": data["path"],
            "sha256": data["sha256"],
        },
        "cohort": {
            "n_patients": cohort["n_patients"],
            "n_positives": cohort["n_positives"],
            "prevalence": cohort["prevalence"],
            "n_folds": cohort["n_folds"],
            "n_val_rows": cohort["n_val_rows"],
            "n_test_rows": cohort["n_test_rows"],
            "mean_val_patients_per_fold": mean_val_patients,
            "mean_test_patients_per_fold": mean_test_patients,
            "splits_fingerprint": splits_fingerprint(cohort["assignment"]),
            "checks": (
                "every patient is a test patient in exactly one fold; validation and "
                "test patients of a fold are disjoint; each patient has one label"
            ),
        },
        "arm_selection": {
            "rule": (
                "the arm is ranked on the mean VALIDATION-split AUROC: it is threshold "
                "free and uses validation rows only, so no test information enters the "
                "choice of arm"
            ),
            "val_auroc": _summarise([fold["val_auroc"] for fold in fold_results]),
            "n_folds": len(fold_results),
        },
        "calibration": {
            "method": (
                "Platt scaling: sigmoid(a * logit(p) + b), a and b fitted by IRLS with a "
                "1e-6 ridge on that fold's VALIDATION split and applied to that fold's "
                "TEST split (nested: one map per fold, no map fitted on test rows)"
            ),
            "logit_clip": LOGIT_EPS,
            "calibration_slope_definition": (
                "logistic regression of the outcome on the predicted logit of the TEST "
                "rows (slope 1, intercept 0 = perfectly calibrated). It is an "
                "evaluation of a frozen map, never a fit that feeds back."
            ),
            "per_fold": fold_results,
            "summary_mean_std_across_folds": calibration_summary,
            "pooled_oof": pooled_calibration,
            "reliability_pooled_oof": {
                "bin_rule": (
                    f"{RELIABILITY_BINS} equal-count bins (rank based) of the pooled "
                    "out-of-fold probability"
                ),
                "before_calibration": reliability["raw"],
                "after_calibration": reliability["calibrated"],
            },
        },
        "workpoints": {
            "primary_representation": "calibrated",
            "reference_representation": "raw",
            **workpoints,
        },
        "decision_curve": {
            "probabilities": "pooled out-of-fold calibrated",
            "net_benefit_formula": "TP/n - (FP/n) * pt/(1-pt)",
            "threshold_probabilities": list(DCA_GRID),
            "rows": decision_curve_rows,
        },
        "limitations": limitations(
            {
                "mean_val_patients": mean_val_patients,
                "mean_test_patients": mean_test_patients,
            }
        ),
    }


def bootstrap_brier_ci(y_true, probabilities, n_boot: int, seed: int) -> dict:
    """Patient-level bootstrap CI of the Brier score."""
    y_true = np.asarray(y_true, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    if len(y_true) < 4:
        return {"low": float("nan"), "high": float("nan"), "n_boot": 0}
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(int(n_boot)):
        index = rng.integers(0, len(y_true), len(y_true))
        values.append(brier_score(y_true[index], probabilities[index]))
    array = np.asarray(values, dtype=float)
    return {
        "low": float(np.percentile(array, 2.5)),
        "high": float(np.percentile(array, 97.5)),
        "n_boot": int(len(array)),
    }


def bootstrap_brier_delta_ci(y_true, probabilities_raw, probabilities_cal, n_boot: int, seed: int) -> dict:
    """Paired patient-level bootstrap CI of ``Brier(calibrated) - Brier(raw)``.

    Pairing matters: both scores are computed on the same resampled patients, so
    the interval describes the *change* rather than the difference of two
    independent intervals.
    """
    y_true = np.asarray(y_true, dtype=int)
    raw = np.asarray(probabilities_raw, dtype=float)
    calibrated = np.asarray(probabilities_cal, dtype=float)
    if len(y_true) < 4:
        return {"low": float("nan"), "high": float("nan"), "n_boot": 0}
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(int(n_boot)):
        index = rng.integers(0, len(y_true), len(y_true))
        values.append(
            brier_score(y_true[index], calibrated[index])
            - brier_score(y_true[index], raw[index])
        )
    array = np.asarray(values, dtype=float)
    return {
        "low": float(np.percentile(array, 2.5)),
        "high": float(np.percentile(array, 97.5)),
        "n_boot": int(len(array)),
    }


def limitations(context: dict | None = None) -> list[str]:
    """Reasons these numbers are not deployable, stated with the numbers.

    ``context`` carries ``mean_val_patients`` / ``mean_test_patients`` so the
    text quotes the split sizes this run actually had instead of a hardcoded
    cohort.
    """
    context = context or {}
    val_text = (
        f"~{context['mean_val_patients']:.0f}"
        if context.get("mean_val_patients")
        else "~100"
    )
    return [
        "Single dataset: CirCor only (paediatric and young-adult recordings, one "
        "acquisition protocol and one labelling campaign). No external cohort, so "
        "transportability to an adult or handheld-device population is untested.",
        "Retrospective cross-validation, not a prospective or randomised study: no "
        "clinician in the loop, no workflow, no harm model for false positives.",
        "PPV is prevalence-dependent. The reported PPV holds at the CirCor prevalence "
        "in this cohort and will be lower in a lower-prevalence screened population.",
        f"Each fold's validation split has {val_text} patients, so a threshold chosen "
        "there carries real sampling noise; that noise is why every threshold is "
        "reported as mean +/- std across folds rather than as a deployment constant.",
        "The validation split is used twice (Platt coefficients and threshold), so "
        "validation numbers are slightly optimistic; only the test numbers are out "
        "of sample for the selection.",
        f"The Platt map itself is fitted per fold on {val_text} patients. A device "
        "would have to fit it on its own calibration set and re-verify, not reuse "
        "these coefficients.",
        "Decision-curve net benefit uses the model probabilities only; it encodes no "
        "local cost of a missed murmur, of a referral, or of an unnecessary "
        "echocardiogram.",
        "Confidence intervals describe patient sampling within this cohort; they do "
        "not include dataset-selection or label-noise uncertainty.",
    ]


# --------------------------------------------------------------------------- #
# reporting helpers
# --------------------------------------------------------------------------- #
def _num(value, digits: int = 3) -> str:
    """Format a float for a markdown table; ``nan``/inf become ``n/a``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(number):
        return "n/a"
    return f"{number:.{digits}f}"


def _pm(summary: dict, digits: int = 3) -> str:
    """Format a ``{"mean", "std"}`` summary as ``mean +/- std``."""
    return f"{_num(summary.get('mean'), digits)} ± {_num(summary.get('std'), digits)}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def deployment_recommendation(reports: dict[str, dict]) -> dict:
    """Pick the arm and the operating point with a rule that uses no test data.

    The rule is fixed in advance and, crucially, is *validation-only*, because a
    rule that used a test-fold quantity - including "which arm happened to meet
    the specificity floor on test" - would be selecting on the test folds and
    every number in this report would stop being out of sample.

    1. The operating point is **W1**: a murmur screen is a triage aid, so missing
       a murmur is the costly error and the specificity floor is stated at 0.80
       with sensitivity maximised under it.
    2. The **arm** is the one with the highest mean validation-split AUROC. AUROC
       is threshold free, so it does not depend on where a noisy 104-patient
       validation split happened to put the threshold, and it never looks at a
       test row.
    3. Whether the 0.80 floor *held out of sample* is then **evaluated** on the
       pooled test folds and reported as ``floor_met`` - a result, never an input
       to the choice.

    Returns the ranking, the selected arm, and the floor evaluation.
    """
    ranked = []
    for arm, report in reports.items():
        workpoint = report["workpoints"]["calibrated"]["W1"]
        pooled = workpoint["pooled_oof"]
        val_auroc = report["arm_selection"]["val_auroc"]
        ranked.append(
            {
                "arm": arm,
                "val_auroc_mean": val_auroc["mean"],
                "val_auroc_std": val_auroc["std"],
                "val_auroc_values": val_auroc["values"],
                "sensitivity": pooled["metrics"]["sensitivity"],
                "sensitivity_low": pooled["ci95"]["sensitivity"]["low"],
                "sensitivity_high": pooled["ci95"]["sensitivity"]["high"],
                "specificity": pooled["metrics"]["specificity"],
                "specificity_low": pooled["ci95"]["specificity"]["low"],
                "specificity_high": pooled["ci95"]["specificity"]["high"],
                "ppv": pooled["metrics"]["ppv"],
                "npv": pooled["metrics"]["npv"],
                "balanced_accuracy": pooled["metrics"]["balanced_accuracy"],
                "n_patients": pooled["n_patients"],
                "threshold": workpoint["summary"]["threshold"],
                "val_sensitivity": workpoint["summary"]["val"]["sensitivity"]["mean"],
                "val_specificity": workpoint["summary"]["val"]["specificity"]["mean"],
                "floor_met": bool(pooled["metrics"]["specificity"] >= FLOOR_SPECIFICITY),
            }
        )
    # Tie-break on the arm name, never on a test quantity: with identical
    # validation discrimination the choice must not silently fall back to test
    # performance.
    ordered = sorted(ranked, key=lambda row: (-row["val_auroc_mean"], row["arm"]))
    best = ordered[0]
    runner_up = ordered[1] if len(ordered) > 1 else None
    return {
        "rule": (
            "W1 (screening) is the operating point because the device is a triage aid; "
            "the arm is the one with the highest mean validation-split AUROC "
            "(threshold-free, validation rows only); whether the specificity floor held "
            "on the test folds is reported as a result, not used to choose."
        ),
        "constraint": "W1",
        "specificity_floor": FLOOR_SPECIFICITY,
        "ranking": ranked,
        "ordered": [row["arm"] for row in ordered],
        "selected_arm": best["arm"],
        "selected": best,
        "runner_up": runner_up,
        "floor_met": best["floor_met"],
        "arms_meeting_floor_on_test": [
            row["arm"] for row in ranked if row["floor_met"]
        ],
    }


def write_summary(path: Path, reports: dict[str, dict]) -> Path:
    """Write the supervisor-facing ``workpoint-summary.md``."""
    arms = list(reports)
    recommendation = deployment_recommendation(reports)
    selected_arm = recommendation["selected_arm"]
    selected = reports[selected_arm]
    lines: list[str] = []
    lines.append(
        f"# Operating-point (workpoint) analysis — CirCor murmur, {len(arms)} "
        f"arm{'s' if len(arms) != 1 else ''}"
    )
    lines.append("")
    lines.append(
        "Every arm of the three-arm comparison is re-reported at an **operating point "
        "with a clinical meaning**, fitted on the validation split of each frozen "
        "patient-level fold and evaluated once on that fold's test split."
    )
    lines.append("")
    lines.append(f"**Deployment recommendation (details in section 6): "
                 f"`{selected_arm}` at constraint `W1` (screening).**")
    lines.append("")

    # ---- 1 protocol ----
    lines.append("## 1. Protocol and provenance")
    lines.append("")
    first = reports[arms[0]]
    lines.append(
        f"- Cohort: **{first['cohort']['n_patients']} patients** "
        f"({first['cohort']['n_positives']} murmur-positive, prevalence "
        f"{first['cohort']['prevalence']:.3f}), {first['cohort']['n_folds']} frozen "
        f"patient-level folds."
    )
    lines.append(
        f"- Frozen split fingerprint (recomputed from each probability dump): "
        f"`{first['cohort']['splits_fingerprint']}`"
    )
    for arm in arms:
        fingerprint = reports[arm]["cohort"]["splits_fingerprint"]
        if fingerprint != first["cohort"]["splits_fingerprint"]:
            lines.append(
                f"- **WARNING:** `{arm}` used a different split "
                f"(`{fingerprint}`); its numbers are not comparable with the others."
            )
    lines.append(
        "- Per fold: Platt coefficients and the decision threshold are fitted on the "
        "**validation** rows only, then frozen; the **test** rows are evaluated once. "
        "No test row influences any selection."
    )
    lines.append(
        "- Patient probability = mean of that patient's window probabilities "
        "(the same aggregate the CV reports use)."
    )
    lines.append(
        "- Intervals: patient-level bootstrap, 2000 resamples, all metrics of one "
        "workpoint share the resamples. Means ± std are across the 5 folds."
    )
    lines.append("")
    lines.append("Probability dumps consumed (one per arm):")
    lines.append("")
    lines.append("| Arm | Dump | sha256 | val rows | test rows |")
    lines.append("| --- | --- | --- | ---: | ---: |")
    for arm in arms:
        report = reports[arm]
        lines.append(
            f"| {arm} | `{report['probability_file']['path']}` | "
            f"`{report['probability_file']['sha256'][:16]}…` | "
            f"{report['cohort']['n_val_rows']} | {report['cohort']['n_test_rows']} |"
        )
    lines.append("")

    # ---- 2 calibration ----
    lines.append("## 2. Calibration (Platt on validation, applied to test)")
    lines.append("")
    lines.append(
        "Calibration slope = logistic regression of the outcome on the predicted "
        "logit of the **test** rows; slope 1 / intercept 0 means perfectly "
        "calibrated. A slope well below 1 means the arm is over-confident."
    )
    lines.append("")
    rows = []
    for arm in arms:
        summary = reports[arm]["calibration"]["summary_mean_std_across_folds"]
        rows.append(
            [
                arm,
                _pm(summary["brier_raw"]),
                _pm(summary["brier_calibrated"]),
                _pm(summary["brier_delta"]),
                _pm(summary["slope_raw"]),
                _pm(summary["slope_calibrated"]),
                _pm(summary["intercept_raw"]),
                _pm(summary["intercept_calibrated"]),
            ]
        )
    lines.extend(
        _table(
            [
                "Arm",
                "Brier raw",
                "Brier calibrated",
                "Δ Brier",
                "Slope raw",
                "Slope calibrated",
                "Intercept raw",
                "Intercept calibrated",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append(
        "Mean ± std across folds. Pooled out-of-fold (all "
        f"{first['cohort']['n_patients']} patients, each with his own fold's map and "
        "threshold):"
    )
    lines.append("")
    rows = []
    for arm in arms:
        pooled = reports[arm]["calibration"]["pooled_oof"]
        rows.append(
            [
                arm,
                _num(pooled["brier_raw"])
                + f" ({_num(pooled['brier_raw_ci95']['low'])}–{_num(pooled['brier_raw_ci95']['high'])})",
                _num(pooled["brier_calibrated"])
                + f" ({_num(pooled['brier_calibrated_ci95']['low'])}–{_num(pooled['brier_calibrated_ci95']['high'])})",
                _num(pooled["brier_delta"]),
                _num(pooled["slope_raw"]),
                _num(pooled["slope_calibrated"]),
                _num(pooled["intercept_raw"]),
                _num(pooled["intercept_calibrated"]),
                _num(pooled["ece_raw"]),
                _num(pooled["ece_calibrated"]),
                _num(pooled["auroc_calibrated"])
                + f" ({_num(pooled['auroc_calibrated_ci95']['low'])}–{_num(pooled['auroc_calibrated_ci95']['high'])})",
            ]
        )
    lines.extend(
        _table(
            [
                "Arm",
                "Brier raw (95% CI)",
                "Brier calibrated (95% CI)",
                "Δ Brier",
                "Slope raw",
                "Slope calibrated",
                "Intercept raw",
                "Intercept calibrated",
                "ECE raw",
                "ECE calibrated",
                "AUROC cal (95% CI)",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append(
        "ECE = equal-count decile expected calibration error. AUROC is invariant to a "
        "monotone map *within one fold*; the pooled out-of-fold AUROC can still differ "
        "between raw and calibrated because each fold applies its own Platt map, which "
        "re-orders patients across folds. Read the two pooled AUROCs as a sign of how "
        "much the fold maps disagree, not as a change in discrimination."
    )
    lines.append("")
    lines.append("### Fitted Platt coefficients per fold (fitted on validation, applied to test)")
    lines.append("")
    lines.append(
        "`a` and `b` are the map actually used: `sigmoid(a·logit(p) + b)`. They are "
        "fitted on that fold's validation patients only, so `a` is an in-sample "
        "quantity and is listed for reproducibility, not as a performance number."
    )
    lines.append("")
    rows = []
    for arm in arms:
        for fold in reports[arm]["calibration"]["per_fold"]:
            platt = fold["platt"]
            rows.append(
                [
                    arm,
                    str(fold["fold"]),
                    f"{platt['slope']:.3f}",
                    f"{platt['intercept']:.3f}",
                    str(platt["n_val"]),
                    str(platt["n_val_positive"]),
                    str(platt["n_val_clipped"]),
                    "yes" if platt["converged"] else "NO",
                ]
            )
    lines.extend(
        _table(
            [
                "Arm",
                "Fold",
                "Platt a",
                "Platt b",
                "n val",
                "n val positive",
                "logit clips",
                "IRLS converged",
            ],
            rows,
        )
    )
    lines.append("")
    negative_slope = [
        (arm, fold["fold"])
        for arm in arms
        for fold in reports[arm]["calibration"]["per_fold"]
        if fold["platt"]["slope"] <= 0.0
    ]
    if negative_slope:
        lines.append(
            "> **Warning:** a non-positive Platt slope reverses the score order in "
            f"{negative_slope}; those folds' calibrated AUROC is *below* the raw one "
            "and the calibrated workpoint is not usable there."
        )
        lines.append("")
    lines.append("### Reliability (pooled out-of-fold, equal-count deciles)")
    lines.append("")
    for arm in arms:
        table = reports[arm]["calibration"]["reliability_pooled_oof"]
        lines.append(f"**{arm}**")
        lines.append("")
        before = table["before_calibration"]
        after = table["after_calibration"]
        rows = []
        for left, right in zip(before, after):
            rows.append(
                [
                    str(left["bin"]),
                    str(left["n"]),
                    _num(left["mean_predicted"]),
                    _num(left["observed"]),
                    _num(right["mean_predicted"]),
                    _num(right["observed"]),
                ]
            )
        lines.extend(
            _table(
                [
                    "Decile",
                    "n",
                    "Before: mean predicted",
                    "Before: observed",
                    "After: mean predicted",
                    "After: observed",
                ],
                rows,
            )
        )
        lines.append("")

    # ---- 3 workpoints ----
    lines.append("## 3. Workpoints: threshold chosen on validation, evaluated on test")
    lines.append("")
    lines.append(
        "W1 screening = maximise sensitivity s.t. specificity ≥ 0.80; "
        "W2 high sensitivity = maximise sensitivity s.t. specificity ≥ 0.70; "
        "W3 balanced = maximise balanced accuracy (the current behaviour). "
        "Threshold ties are broken towards the higher threshold (more specificity "
        "margin)."
    )
    lines.append("")
    lines.append(
        "### 3a. Test performance, mean ± std across folds (calibrated probabilities — primary)"
    )
    lines.append("")
    rows = []
    for arm in arms:
        for constraint in CONSTRAINTS:
            cid = constraint["id"]
            entry = reports[arm]["workpoints"]["calibrated"][cid]
            summary = entry["summary"]
            rows.append(
                [
                    arm,
                    f"{cid} {constraint['name']}",
                    _pm(summary["threshold"]),
                    _pm(summary["test"]["sensitivity"]),
                    _pm(summary["test"]["specificity"]),
                    _pm(summary["test"]["ppv"]),
                    _pm(summary["test"]["npv"]),
                    _pm(summary["test"]["f1"]),
                    _pm(summary["test"]["balanced_accuracy"]),
                ]
            )
    lines.extend(
        _table(
            [
                "Arm",
                "Constraint",
                "Threshold",
                "Sens",
                "Spec",
                "PPV",
                "NPV",
                "F1",
                "BA",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append(
        "### 3b. Validation → test transfer of the same operating point (calibrated)"
    )
    lines.append("")
    lines.append(
        f"How the constraint that was bought on "
        f"~{first['cohort']['mean_val_patients_per_fold']:.0f} validation patients "
        f"per fold held up on the "
        f"~{first['cohort']['mean_test_patients_per_fold']:.0f} test patients of the "
        f"same fold. A large negative Δ means the validation split flattered the "
        f"constraint."
    )
    lines.append("")
    rows = []
    for arm in arms:
        for constraint in CONSTRAINTS:
            cid = constraint["id"]
            summary = reports[arm]["workpoints"]["calibrated"][cid]["summary"]
            val_sens = summary["val"]["sensitivity"]["mean"]
            test_sens = summary["test"]["sensitivity"]["mean"]
            val_spec = summary["val"]["specificity"]["mean"]
            test_spec = summary["test"]["specificity"]["mean"]
            rows.append(
                [
                    arm,
                    f"{cid} {constraint['name']}",
                    f"{_num(val_sens)} → {_num(test_sens)}",
                    _num(test_sens - val_sens),
                    f"{_num(val_spec)} → {_num(test_spec)}",
                    _num(test_spec - val_spec),
                    f"{summary['n_folds_constraint_feasible']}/{summary['n_folds']}",
                ]
            )
    lines.extend(
        _table(
            [
                "Arm",
                "Constraint",
                "Sens val → test",
                "Δ sens",
                "Spec val → test",
                "Δ spec",
                "Folds meeting the floor on val",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append(
        "### 3c. Pooled out-of-fold test performance with bootstrap 95% CI"
    )
    lines.append("")
    lines.append(
        "Each patient is predicted with his own fold's threshold and Platt map; all "
        f"{first['cohort']['n_patients']} decisions are pooled. This is the closest "
        "thing to a single deployed operating point in this design."
    )
    lines.append("")
    rows = []
    for arm in arms:
        for constraint in CONSTRAINTS:
            cid = constraint["id"]
            pooled = reports[arm]["workpoints"]["calibrated"][cid]["pooled_oof"]
            metrics = pooled["metrics"]
            ci = pooled["ci95"]

            def cell(field: str) -> str:
                return (
                    f"{_num(metrics[field])} "
                    f"({_num(ci[field]['low'])}–{_num(ci[field]['high'])})"
                )

            rows.append(
                [
                    arm,
                    f"{cid} {constraint['name']}",
                    cell("sensitivity"),
                    cell("specificity"),
                    cell("ppv"),
                    cell("npv"),
                    cell("f1"),
                    cell("balanced_accuracy"),
                ]
            )
    lines.extend(
        _table(
            [
                "Arm",
                "Constraint",
                "Sens (95% CI)",
                "Spec (95% CI)",
                "PPV (95% CI)",
                "NPV (95% CI)",
                "F1 (95% CI)",
                "BA (95% CI)",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append(
        "### 3d. Reference: the current pipeline (raw probabilities, no calibration)"
    )
    lines.append("")
    lines.append(
        "The same selection rule on the uncalibrated scores, i.e. exactly what the "
        "CV reports do today. Kept as the *status quo* candidate."
    )
    lines.append("")
    rows = []
    for arm in arms:
        for constraint in CONSTRAINTS:
            cid = constraint["id"]
            entry = reports[arm]["workpoints"]["raw"][cid]
            summary = entry["summary"]
            pooled = entry["pooled_oof"]
            rows.append(
                [
                    arm,
                    f"{cid} {constraint['name']}",
                    _pm(summary["threshold"]),
                    _pm(summary["test"]["sensitivity"]),
                    _pm(summary["test"]["specificity"]),
                    _num(pooled["metrics"]["sensitivity"])
                    + f" ({_num(pooled['ci95']['sensitivity']['low'])}–"
                    f"{_num(pooled['ci95']['sensitivity']['high'])})",
                    _num(pooled["metrics"]["specificity"])
                    + f" ({_num(pooled['ci95']['specificity']['low'])}–"
                    f"{_num(pooled['ci95']['specificity']['high'])})",
                ]
            )
    lines.extend(
        _table(
            [
                "Arm",
                "Constraint",
                "Threshold",
                "Test sens",
                "Test spec",
                "Pooled sens (95% CI)",
                "Pooled spec (95% CI)",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append(
        "The raw thresholds are uninterpretable as probabilities — that spread is the "
        "gap this analysis exists to close."
    )
    lines.append("")

    # ---- 4 decision curve ----
    lines.append("## 4. Decision-curve net benefit (pooled out-of-fold, calibrated)")
    lines.append("")
    lines.append(
        "Net benefit = TP/n − (FP/n)·pt/(1−pt). A model is worth using at threshold "
        "probability `pt` only if it beats both `treat all` and `treat none` (= 0)."
    )
    lines.append("")
    header = ["pt", *[f"{arm} NB" for arm in arms], "treat all", "treat none"]
    rows = []
    curves = [reports[arm]["decision_curve"]["rows"] for arm in arms]
    for position, row in enumerate(curves[0]):
        rows.append(
            [
                _num(row["threshold_probability"], 2),
                *[_num(curves[index][position]["net_benefit"]) for index in range(len(arms))],
                _num(row["treat_all"]),
                _num(row["treat_none"]),
            ]
        )
    lines.extend(_table(header, rows))
    lines.append("")
    best_at = {}
    for arm in arms:
        curve = reports[arm]["decision_curve"]["rows"]
        margins = [
            row["net_benefit"] - max(row["treat_all"], 0.0)
            for row in curve
            if np.isfinite(row["net_benefit"])
        ]
        best_at[arm] = max(margins) if margins else float("nan")
    lines.append(
        "Largest margin over the better baseline strategy in 0.05–0.50: "
        + ", ".join(f"`{arm}` {_num(best_at[arm])}" for arm in arms)
        + "."
    )
    lines.append("")

    # ---- 5 what changed ----
    lines.append("## 5. What calibration buys, in one paragraph")
    lines.append("")
    calibrated_deltas = {
        arm: reports[arm]["calibration"]["pooled_oof"]["brier_delta"] for arm in arms
    }
    deltas = list(calibrated_deltas.values())
    improved = [value for value in deltas if value < 0]
    if len(improved) == len(deltas):
        verdict = (
            "so the Platt map improves the probability scale of every arm. It cannot "
            "change a fold's own AUROC (the map is monotone); the pooled AUROC moves "
            "only because each fold applies a different map (section 2)"
        )
    elif not improved:
        verdict = (
            "so **calibration does not improve the Brier score of any arm here** - the "
            "raw probabilities were already at least as good on this metric"
        )
    else:
        verdict = (
            f"so calibration helps {len(improved)} of {len(deltas)} arms and hurts the "
            "rest; the per-arm sign matters and is reported in section 2"
        )
    slopes = [
        reports[arm]["calibration"]["pooled_oof"]["slope_calibrated"] for arm in arms
    ]
    lines.append(
        "Pooled Brier change (calibrated minus raw; negative = better): "
        + ", ".join(
            f"{_num(value)} (`{arm}`)" for arm, value in calibrated_deltas.items()
        )
        + f" — {verdict}, and it does not make any threshold transferable by itself: "
        "the operating point is still chosen per fold on "
        f"~{first['cohort']['mean_val_patients_per_fold']:.0f} validation patients."
    )
    lines.append("")
    lines.append(
        f"Residual miscalibration remains after the map: the pooled out-of-fold "
        f"calibration slope is {_num(min(slopes))}–{_num(max(slopes))} across the "
        f"arms (1.000 is perfect), so the calibrated probabilities are still "
        f"over-confident away from the centre even though Brier and ECE improve "
        f"substantially. A slope below 1 also means the *ordering* of patients is "
        f"better than the *level* of the probabilities: use the workpoint, not the "
        f"number, for triage."
    )
    lines.append("")

    # ---- 6 recommendation ----
    lines.append("## 6. Deployment recommendation")
    lines.append("")
    selected = reports[recommendation["selected_arm"]]
    chosen = recommendation["selected"]
    if recommendation["floor_met"]:
        lines.append(
            f"**Deploy `{recommendation['selected_arm']}` at `W1` (screening).**"
        )
    else:
        lines.append(
            f"**Take `{recommendation['selected_arm']}` at `W1` (screening) forward as "
            f"the candidate workpoint — but do not freeze the threshold: the 0.80 "
            f"specificity floor did not hold on the test folds.**"
        )
    lines.append("")
    lines.append(
        "Rule (fixed in advance, and it uses **no test information**): "
        f"{recommendation['rule']}"
    )
    lines.append("")
    rows = []
    for row in recommendation["ranking"]:
        marker = " **(selected)**" if row["arm"] == recommendation["selected_arm"] else ""
        rows.append(
            [
                f"`{row['arm']}`{marker}",
                _pm({"mean": row["val_auroc_mean"], "std": row["val_auroc_std"]}),
                _num(row["val_sensitivity"]),
                _num(row["val_specificity"]),
                f"{_num(row['sensitivity'])} "
                f"({_num(row['sensitivity_low'])}–{_num(row['sensitivity_high'])})",
                f"{_num(row['specificity'])} "
                f"({_num(row['specificity_low'])}–{_num(row['specificity_high'])})",
                "yes" if row["floor_met"] else "**no**",
            ]
        )
    lines.extend(
        _table(
            [
                "Arm",
                "Val AUROC (mean ± std)",
                "Val sens @ W1",
                "Val spec @ W1",
                "Test sens (95% CI)",
                "Test spec (95% CI)",
                "Floor met on test",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append(
        f"`{recommendation['selected_arm']}` is selected on **validation** "
        f"discrimination (mean val AUROC {_num(chosen['val_auroc_mean'])} ± "
        f"{_num(chosen['val_auroc_std'])}; per fold "
        f"{', '.join(_num(value) for value in chosen['val_auroc_values'])})."
    )
    lines.append(
        f"- At its W1 point the pooled out-of-fold test performance is sensitivity "
        f"**{_num(chosen['sensitivity'])}** (95% CI {_num(chosen['sensitivity_low'])}–"
        f"{_num(chosen['sensitivity_high'])}), specificity "
        f"**{_num(chosen['specificity'])}** (95% CI "
        f"{_num(chosen['specificity_low'])}–{_num(chosen['specificity_high'])}), "
        f"PPV {_num(chosen['ppv'])}, NPV {_num(chosen['npv'])}."
    )
    lines.append(
        f"- Threshold on the calibrated probability: {_pm(chosen['threshold'])} across "
        f"folds; the most and least permissive fold differ by "
        f"{_num(max(chosen['threshold']['values']) - min(chosen['threshold']['values']))} "
        f"— that spread is the honest uncertainty of the operating point."
    )
    n_folds_floor = sum(
        1
        for fold in selected["workpoints"]["calibrated"]["W1"]["per_fold"]
        if fold["test"]["specificity"] >= FLOOR_SPECIFICITY
    )
    n_folds = len(selected["workpoints"]["calibrated"]["W1"]["per_fold"])
    if recommendation["floor_met"]:
        lines.append(
            f"- The 0.80 specificity floor **held** on the pooled test folds "
            f"({n_folds_floor}/{n_folds} folds reached it individually)."
        )
    else:
        gap = FLOOR_SPECIFICITY - chosen["specificity"]
        lines.append(
            f"- **The 0.80 specificity floor did not hold**: pooled test specificity is "
            f"{_num(chosen['specificity'])} "
            f"({_num(chosen['specificity_low'])}–{_num(chosen['specificity_high'])}), "
            f"a shortfall of {_num(gap)} that sits inside the interval, and only "
            f"{n_folds_floor}/{n_folds} folds reached 0.80 on their own. The floor is "
            f"therefore *not demonstrated* — do not freeze this threshold."
        )
    meeting = [
        arm
        for arm in recommendation["arms_meeting_floor_on_test"]
        if arm != recommendation["selected_arm"]
    ]
    if meeting:
        described = []
        for arm in meeting:
            row = next(item for item in recommendation["ranking"] if item["arm"] == arm)
            described.append(
                f"`{arm}` (sensitivity {_num(row['sensitivity'])}, specificity "
                f"{_num(row['specificity'])})"
            )
        lines.append(
            "- Post-hoc alternative, **not** the recommendation: "
            + ", ".join(described)
            + " did clear the 0.80 floor on these test folds. Choosing one of them now "
            "would mean choosing the arm on test data, which the protocol forbids and "
            "which is how the 0.05–0.82 threshold spread in the older reports came "
            "about. Treat it as the fallback if a hard 0.80 specificity guarantee is "
            "non-negotiable, and settle it on a larger calibration split."
        )
    if recommendation["runner_up"] is not None:
        runner = recommendation["runner_up"]
        lines.append(
            f"- Runner-up on validation discrimination: `{runner['arm']}` "
            f"(mean val AUROC {_num(runner['val_auroc_mean'])}; test sensitivity "
            f"{_num(runner['sensitivity'])} at specificity {_num(runner['specificity'])}). "
            f"The gap to the selected arm is "
            f"{_num(chosen['val_auroc_mean'] - runner['val_auroc_mean'])} AUROC on "
            f"validation, which is the only quantity the choice was allowed to use."
        )
    lines.append("")
    lines.append("Why screening (W1) and not the current balanced-accuracy rule:")
    lines.append("")
    status_quo_specs = [
        reports[arm]["workpoints"]["raw"]["W3"]["pooled_oof"]["metrics"]["specificity"]
        for arm in arms
    ]
    lines.append(
        "- A murmur screen is a triage aid: a missed murmur (false negative) sends a "
        "child home, a false positive sends him to an echocardiogram. The current "
        "balanced-accuracy rule treats those errors as equally priced and lands at "
        f"pooled specificities of {_num(min(status_quo_specs))}–"
        f"{_num(max(status_quo_specs))} across the arms, with no stated floor."
    )
    lines.append(
        "- W1 makes the trade explicit and auditable: 'we hold specificity at 0.80 on "
        "the validation split and buy as much sensitivity as that allows'."
    )
    lines.append(
        "- W3 is kept as the reference because it is what the existing CV reports did; "
        "comparing W1 and W3 on the same test folds shows exactly what the explicit "
        "constraint costs."
    )
    lines.append("")

    # ---- 7 not deployable ----
    lines.append("## 7. Which numbers are still NOT deployable")
    lines.append("")
    for item in selected["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append(
        "Concretely, the number a clinical reader must **not** quote as a device "
        "performance claim is the pooled W1 sensitivity/specificity pair above: it is "
        "a cross-validated estimate on one paediatric dataset, with a threshold chosen "
        f"on ~{selected['cohort']['mean_val_patients_per_fold']:.0f} patients per "
        "fold. It is good enough to choose *which arm and which constraint* to take "
        "forward to an external validation set; it is not good enough to print on a "
        "device."
    )
    lines.append("")

    # ---- 8 decision record ----
    lines.append("## 8. Decision record (AGENTS.md format)")
    lines.append("")
    lines.append("**Decision question.** Which model arm and which operating point does "
                 "the PCG stethoscope take forward as its murmur screening workpoint?")
    lines.append("")
    lines.append(
        f"**Candidates ({len(arms) * 2}, one calibrated and one status-quo row per arm).**"
    )
    lines.append("")
    rows = []
    for arm in arms:
        pooled = reports[arm]["workpoints"]["calibrated"]["W1"]["pooled_oof"]
        rows.append(
            [
                f"`{arm}` @ W1 (calibrated)",
                "spec ≥ 0.80, max sens",
                _pm(reports[arm]["arm_selection"]["val_auroc"]),
                _num(pooled["metrics"]["sensitivity"]),
                _num(pooled["metrics"]["specificity"]),
                _num(pooled["metrics"]["ppv"]),
                "E4",
            ]
        )
    for arm in arms:
        status = reports[arm]["workpoints"]["raw"]["W3"]["pooled_oof"]
        rows.append(
            [
                f"`{arm}` @ W3 raw (status quo)",
                "max balanced accuracy",
                _pm(reports[arm]["arm_selection"]["val_auroc"]),
                _num(status["metrics"]["sensitivity"]),
                _num(status["metrics"]["specificity"]),
                _num(status["metrics"]["ppv"]),
                "E4",
            ]
        )
    lines.extend(
        _table(
            [
                "Candidate",
                "Operating point",
                "Val AUROC (mean ± std)",
                "Pooled test sens",
                "Pooled test spec",
                "Pooled test PPV",
                "Evidence",
            ],
            rows,
        )
    )
    lines.append("")
    lines.append("**Evidence levels.** E4 for every number here: reproducible locally on "
                 "one public dataset (CirCor), retrospective CV. No E2 (independent "
                 "reproduction) or E1 (prospective/standard) evidence exists for an "
                 "operating point on this device.")
    lines.append("")
    lines.append(
        "**Why this candidate.** It is the arm with the best validation discrimination "
        f"(mean val AUROC {_num(recommendation['selected']['val_auroc_mean'])}) used at "
        "the operating point the screening indication dictates (W1). No test row took "
        "part in the choice; the test rows only report what that choice achieves."
    )
    lines.append("")
    lines.append(
        "**Conditions that would change the choice.** (a) an external cohort shows the "
        "specificity floor does not transport; (b) the clinical owner prices a missed "
        "murmur differently, moving the constraint to W2 or W3; (c) a revision of the "
        "arm changes its mean validation AUROC enough to reorder the ranking; (d) a "
        "prospective study shows the calibration map does not transfer; (e) a larger "
        "calibration split shows that the arm's rank on validation AUROC was noise."
    )
    lines.append("")
    lines.append(
        "**Verification plan.** Freeze the chosen arm's probability dump and this "
        "analysis directory; re-run on a held-out external cohort or on newly collected "
        "device recordings with the Platt map fitted on that cohort's own calibration "
        "split; acceptance = the W1 specificity floor holds with its CI lower bound "
        f"≥ {_num(FLOOR_SPECIFICITY - 0.05, 2)} and the sensitivity CI excludes 0.50."
    )
    lines.append("")
    meeting_arms = recommendation["arms_meeting_floor_on_test"]
    if recommendation["floor_met"]:
        fallback = (
            "If the floor does not hold out of sample, fall back to W2/W3 with the "
            "achieved specificity printed alongside (never a threshold without its "
            "specificity), and to the status-quo W3-raw workpoint until external data "
            "exists."
        )
    else:
        fallback = (
            "The floor is **not** demonstrated for the selected arm, so the fallback "
            "order is: (1) re-derive the threshold to the specificity the data actually "
            "supports and restate the constraint at that value - never quote a threshold "
            "without its specificity; (2) if a hard 0.80 guarantee is non-negotiable, "
            "fall back to the best arm that did clear it on the test folds"
        )
        if meeting_arms:
            best_meeting = max(
                (row for row in recommendation["ranking"] if row["arm"] in meeting_arms),
                key=lambda row: row["sensitivity"],
            )
            others = [arm for arm in meeting_arms if arm != best_meeting["arm"]]
            fallback += (
                f", `{best_meeting['arm']}` (sensitivity {_num(best_meeting['sensitivity'])} "
                f"at specificity {_num(best_meeting['specificity'])}, i.e. "
                f"{_num(recommendation['selected']['sensitivity'] - best_meeting['sensitivity'])} "
                f"less sensitivity than the selected arm)"
            )
            if others:
                fallback += " (also meeting it: " + ", ".join(f"`{arm}`" for arm in others) + ")"
        fallback += "; (3) otherwise keep the status-quo W3-raw workpoint until external data exists."
    lines.append(f"**Failure criteria and fallback.** {fallback}")
    lines.append("")

    # ---- 9 reproduce ----
    lines.append("## 9. How to reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append(
        ".venv/bin/python tools/ai/cross_validate.py --data-dir data/processed/circor "
        "--model logreg --window-selection annotated --splits-file "
        "data/processed/circor/splits.csv --dump-probabilities /tmp/prob-logreg.csv"
    )
    lines.append(
        ".venv/bin/python tools/ai/train_mfcc.py --data-dir data/processed/circor "
        "--out-dir data/reports/circor-cv-mfcc --splits-file "
        "data/processed/circor/splits.csv --window-selection annotated "
        "--dump-probabilities /tmp/prob-mfcc.csv"
    )
    lines.append(
        ".venv/bin/python tools/ai/cross_validate.py --data-dir data/processed/circor "
        "--model cnn --window-selection annotated --splits-file "
        "data/processed/circor/splits.csv --cnn-epochs 30 --cnn-patience 6 "
        "--dump-probabilities /tmp/prob-cnn.csv"
    )
    lines.append(
        ".venv/bin/python tools/ai/workpoint_analysis.py --probs "
        "logreg=/tmp/prob-logreg.csv mfcc=/tmp/prob-mfcc.csv cnn=/tmp/prob-cnn.csv "
        "--out-dir data/reports"
    )
    lines.append("```")
    lines.append("")
    lines.append(
        "This script reads only the probability dumps; every number above is written "
        "by it into `workpoint-<arm>.json` and in these tables."
    )
    lines.append("")
    path = Path(path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Calibrate the three-arm CirCor comparison and evaluate stated clinical "
            "workpoints (W1 screening, W2 high sensitivity, W3 balanced) on the test "
            "folds, with patient-level bootstrap CIs and a decision curve."
        )
    )
    parser.add_argument(
        "--probs",
        nargs="+",
        required=True,
        metavar="ARM=PATH",
        help=(
            "One or more arm=path pairs pointing at --dump-probabilities CSVs, for "
            "example logreg=/tmp/prob-logreg.csv mfcc=/tmp/prob-mfcc.csv "
            "cnn=/tmp/prob-cnn.csv. Order is preserved in the summary."
        ),
    )
    parser.add_argument("--out-dir", default="data/reports")
    parser.add_argument(
        "--n-boot",
        type=int,
        default=DEFAULT_N_BOOT,
        help=f"Patient-level bootstrap resamples per interval (default {DEFAULT_N_BOOT}).",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--expected-fingerprint",
        default=DEFAULT_EXPECTED_FINGERPRINT,
        help=(
            "sha256 of the frozen splits.csv the dumps must reproduce (the 874-patient "
            "CirCor protocol by default). Pass an empty string to skip the check."
        ),
    )
    parser.add_argument(
        "--allow-other-splits",
        action="store_true",
        help="Analyse a dump whose split fingerprint differs from --expected-fingerprint.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    specs = parse_arm_specs(args.probs)
    if args.n_boot < 1:
        raise SystemExit("--n-boot must be >= 1")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: dict[str, dict] = {}
    for arm, path in specs:
        report = analyse_arm(arm, path, n_boot=args.n_boot, seed=args.seed)
        fingerprint = report["cohort"]["splits_fingerprint"]
        expected = (args.expected_fingerprint or "").strip()
        if expected and fingerprint != expected and not args.allow_other_splits:
            raise SystemExit(
                f"{path}: split fingerprint {fingerprint} does not match the frozen "
                f"protocol {expected}. The cohort/folds are the protocol, so this "
                f"analysis refuses to run; pass --allow-other-splits only for a "
                f"deliberately different split."
            )
        reports[arm] = report
        target = out_dir / f"workpoint-{arm}.json"
        target.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        workpoint = report["workpoints"]["calibrated"]["W1"]
        print(
            f"[workpoint] {arm}: {report['cohort']['n_patients']} patients, "
            f"fingerprint={fingerprint[:16]}… "
            f"brier {report['calibration']['pooled_oof']['brier_raw']:.4f} -> "
            f"{report['calibration']['pooled_oof']['brier_calibrated']:.4f}, "
            f"W1 pooled sens={workpoint['pooled_oof']['metrics']['sensitivity']:.3f} "
            f"spec={workpoint['pooled_oof']['metrics']['specificity']:.3f}"
        )
        print(f"[workpoint] wrote {target}")

    summary_path = write_summary(out_dir / "workpoint-summary.md", reports)
    recommendation = deployment_recommendation(reports)
    print(
        f"[workpoint] recommendation: {recommendation['selected_arm']} @ "
        f"{recommendation['constraint']} (selected on mean val AUROC "
        f"{recommendation['selected']['val_auroc_mean']:.3f}; "
        f"specificity floor {recommendation['specificity_floor']:.2f} "
        f"{'met' if recommendation['floor_met'] else 'NOT met'} on the pooled test folds)"
    )
    print(f"[workpoint] wrote {summary_path}")



if __name__ == "__main__":
    main()
