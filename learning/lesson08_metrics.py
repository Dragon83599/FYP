"""Lesson 08: binary classification metrics.

Run from the repository root:
    .venv\\Scripts\\python.exe learning\\lesson08_metrics.py
"""

# 10 patients: 4 positive (1) and 6 negative (0).
labels = [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]

# Model scores for the positive class. Higher means "more likely class 1".
scores = [0.90, 0.80, 0.70, 0.40, 0.60, 0.30, 0.20, 0.10, 0.05, 0.45]


def metrics_at_threshold(labels, scores, threshold):
    """Return a confusion matrix and common binary metrics."""
    tp = tn = fp = fn = 0

    for label, score in zip(labels, scores):
        prediction = 1 if score >= threshold else 0

        if label == 1 and prediction == 1:
            tp += 1
        elif label == 0 and prediction == 0:
            tn += 1
        elif label == 0 and prediction == 1:
            fp += 1
        elif label == 1 and prediction == 0:
            fn += 1

    total = len(labels)
    accuracy = (tp + tn) / total
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    balanced_accuracy = (recall + specificity) / 2

    return {
        "threshold": threshold,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": f1,
        "balanced_accuracy": balanced_accuracy,
    }


for threshold in (0.5, 0.3):
    result = metrics_at_threshold(labels, scores, threshold)
    print(f"=== threshold = {threshold} ===")
    print(
        f"TP={result['tp']} TN={result['tn']} "
        f"FP={result['fp']} FN={result['fn']}"
    )
    print(f"accuracy           = {result['accuracy']:.4f}")
    print(f"precision          = {result['precision']:.4f}")
    print(f"recall / sensitivity = {result['recall']:.4f}")
    print(f"specificity        = {result['specificity']:.4f}")
    print(f"F1                 = {result['f1']:.4f}")
    print(f"balanced accuracy  = {result['balanced_accuracy']:.4f}")
    print()
