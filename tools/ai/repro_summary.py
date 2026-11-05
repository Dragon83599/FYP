#!/usr/bin/env python3
"""Summarise reproduction runs with macro F1 as the headline metric.

Why F1 and not accuracy
-----------------------
ADR-053 froze macro F1 as the metric that decides whether a model is good. The
reproduction is the one place where accuracy still appears, and only because the
paper reports accuracy and nothing else: subtracting an F1 from 98.65 % would be
subtracting two different quantities. So this tool prints both, with F1 first,
and marks the accuracy column as "only for comparison with the paper".

The pooled numbers are computed from the out-of-fold predictions stored in each
report (every clip is in exactly one test fold), so they are not an average of
per-fold F1s: the confusion matrix is summed first and F1 is computed once. That
is the number to quote; averaging fold-level F1 over-weights folds that happen
to have few positives for a class.

    .venv/bin/python tools/ai/repro_summary.py \
        data/reports/repro-ma2023-fixed100/report.json \
        data/reports/repro-ma2023-fixed100-seed1042/report.json \
        ... [--json out.json] [--markdown]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

PAPER_ACC = 98.65


def pooled(report: dict) -> dict:
    """Sum the out-of-fold confusion matrices, then compute metrics once."""
    classes = report["clips"] and sorted(
        {clip["label"] for clip in report["clips"]},
        key=lambda name: next(c["label_index"] for c in report["clips"] if c["label"] == name),
    )
    labels = [clip["label"] for clip in report["clips"]]
    index_of = {}
    for clip in report["clips"]:
        index_of.setdefault(clip["label"], clip["label_index"])
    k = len(index_of)
    names = [None] * k
    for label, index in index_of.items():
        names[index] = label

    cm = np.zeros((k, k), dtype=np.int64)
    covered = 0
    for fold in report["folds"]:
        for position, prediction in zip(fold["test_index"], fold["test_pred"]):
            truth = report["clips"][position]["label_index"]
            cm[truth, prediction] += 1
        covered += len(fold["test_index"])

    total = int(cm.sum())
    accuracy = float(np.trace(cm) / total) if total else float("nan")
    per_class = {}
    f1s = []
    for i, name in enumerate(names):
        tp = int(cm[i, i])
        fp = int(cm[:, i].sum()) - tp
        fn = int(cm[i, :].sum()) - tp
        denom = 2 * tp + fp + fn
        f1 = 2 * tp / denom if denom else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        per_class[name] = {"precision_pct": round(precision * 100, 2),
                           "recall_pct": round(recall * 100, 2),
                           "f1_pct": round(f1 * 100, 2),
                           "support": int(cm[i, :].sum())}
        f1s.append(f1)

    return {
        "n_clips": total,
        "n_oof_covered": covered,
        "class_order": names,
        "accuracy_pct": round(accuracy * 100, 2),
        "macro_f1_pct": round(float(np.mean(f1s)) * 100, 2),
        "per_class": per_class,
        "confusion": cm.tolist(),
    }


def t_ci95(values: list[float]) -> tuple[float, float]:
    values = np.asarray(values, dtype=np.float64)
    if values.size < 2:
        return float("nan"), float("nan")
    from scipy import stats

    half = float(stats.t.ppf(0.975, values.size - 1) * values.std(ddof=1) / np.sqrt(values.size))
    return float(values.mean() - half), float(values.mean() + half)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, default=None, help="write the summary as JSON")
    args = parser.parse_args(argv)

    rows = []
    for path in args.reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        metrics = pooled(report)
        rows.append({"report": str(path), "run": path.parent.name, **metrics})

    f1s = [row["macro_f1_pct"] for row in rows]
    accs = [row["accuracy_pct"] for row in rows]
    f1_ci = t_ci95(f1s)
    summary = {
        "attempts": rows,
        "attempt_mean_macro_f1_pct": round(float(np.mean(f1s)), 3),
        "attempt_std_macro_f1_pct": round(float(np.std(f1s, ddof=1)), 3) if len(f1s) > 1 else None,
        "attempt_ci95_macro_f1_pct": [round(x, 3) for x in f1_ci],
        "attempt_mean_accuracy_pct": round(float(np.mean(accs)), 3),
        "paper_accuracy_pct": PAPER_ACC,
        "note": "Macro F1 is the headline (ADR-053); accuracy is only comparable with the paper.",
    }

    print("| 运行 | macro F1（主指标）| accuracy（仅对文对齐）| 与 98.65 的差 |")
    print("| --- | ---: | ---: | ---: |")
    for row in rows:
        print(f"| {row['run']} | **{row['macro_f1_pct']:.2f}** | {row['accuracy_pct']:.2f} | "
              f"{row['accuracy_pct'] - PAPER_ACC:+.2f} |")
    print(f"| **attempt 均值（n={len(rows)}）** | **{np.mean(f1s):.2f}** | {np.mean(accs):.2f} | "
          f"{np.mean(accs) - PAPER_ACC:+.2f} |")
    print()
    print(f"macro F1 的 attempt 间 95% CI：[{f1_ci[0]:.2f}, {f1_ci[1]:.2f}]")

    print("\n逐类 F1（pooled，各次运行的单次结果）：")
    for row in rows:
        detail = "　".join(f"{name} {stats['f1_pct']:.2f}" for name, stats in row["per_class"].items())
        print(f"  {row['run']}: {detail}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        print(f"\nJSON -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
