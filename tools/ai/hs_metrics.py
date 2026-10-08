"""Binary classification metrics implemented with NumPy only."""

from __future__ import annotations

import numpy as np


def confusion(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[int, int, int, int]:
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    tp = int(np.sum((y_true == 1) & (y_pred == 1)))
    tn = int(np.sum((y_true == 0) & (y_pred == 0)))
    fp = int(np.sum((y_true == 0) & (y_pred == 1)))
    fn = int(np.sum((y_true == 1) & (y_pred == 0)))
    return tp, tn, fp, fn


def binary_metrics(y_true: np.ndarray, y_prob: np.ndarray, threshold: float = 0.5) -> dict:
    y_true = np.asarray(y_true, dtype=int)
    y_prob = np.asarray(y_prob, dtype=float)
    y_pred = (y_prob >= threshold).astype(int)
    tp, tn, fp, fn = confusion(y_true, y_pred)
    sensitivity = tp / max(tp + fn, 1)
    specificity = tn / max(tn + fp, 1)
    precision = tp / max(tp + fp, 1)
    recall = sensitivity
    f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    accuracy = (tp + tn) / max(len(y_true), 1)
    return {
        "n": int(len(y_true)),
        "threshold": float(threshold),
        "accuracy": float(accuracy),
        "balanced_accuracy": float((sensitivity + specificity) / 2.0),
        "sensitivity": float(sensitivity),
        "specificity": float(specificity),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "auroc": float(auroc(y_true, y_prob)),
        "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
    }


def auroc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Rank-based AUROC with average ranks for ties."""
    y_true = np.asarray(y_true, dtype=int)
    y_score = np.asarray(y_score, dtype=float)
    positives = int(np.sum(y_true == 1))
    negatives = int(np.sum(y_true == 0))
    if positives == 0 or negatives == 0:
        return float("nan")
    order = np.argsort(y_score, kind="mergesort")
    sorted_scores = y_score[order]
    ranks = np.empty(len(y_score), dtype=float)
    start = 0
    while start < len(sorted_scores):
        end = start + 1
        while end < len(sorted_scores) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0 + 1.0
        start = end
    positive_rank_sum = float(np.sum(ranks[y_true == 1]))
    return (positive_rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def best_threshold(y_true: np.ndarray, y_prob: np.ndarray) -> tuple[float, float]:
    """Return the threshold that maximizes balanced accuracy.

    Ties are broken by distance to 0.5 so that a tiny validation set cannot
    push the threshold to 0 or 1 just because several thresholds score equally.
    """
    candidates = np.unique(np.round(y_prob, 4))
    best_threshold_value = 0.5
    best_score = -1.0
    best_distance = -1.0
    for threshold in candidates:
        score = binary_metrics(y_true, y_prob, threshold)["balanced_accuracy"]
        distance = -abs(float(threshold) - 0.5)
        if score > best_score + 1e-12 or (
            abs(score - best_score) <= 1e-12 and distance > best_distance
        ):
            best_threshold_value = float(threshold)
            best_score = score
            best_distance = distance
    return best_threshold_value, best_score


def bootstrap_ci(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    metric: str = "auroc",
    threshold: float = 0.5,
    n_boot: int = 2000,
    seed: int = 20261008,
    alpha: float = 0.05,
) -> dict:
    """Patient-level bootstrap confidence interval for a binary metric."""
    y_true = np.asarray(y_true, dtype=int)
    y_prob = np.asarray(y_prob, dtype=float)
    if len(y_true) < 4 or len(np.unique(y_true)) < 2:
        return {"low": float("nan"), "high": float("nan"), "n_boot": 0}
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(n_boot):
        index = rng.integers(0, len(y_true), len(y_true))
        sample_true = y_true[index]
        if len(np.unique(sample_true)) < 2:
            continue
        sample_prob = y_prob[index]
        if metric == "auroc":
            values.append(auroc(sample_true, sample_prob))
        else:
            values.append(binary_metrics(sample_true, sample_prob, threshold)[metric])
    if not values:
        return {"low": float("nan"), "high": float("nan"), "n_boot": 0}
    values = np.asarray(values, dtype=float)
    return {
        "low": float(np.percentile(values, 100 * alpha / 2)),
        "high": float(np.percentile(values, 100 * (1 - alpha / 2))),
        "n_boot": int(len(values)),
    }
