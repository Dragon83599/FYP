#!/usr/bin/env python3
"""Measure the accuracy cost of INT8 quantization on the frozen folds.

ADR-007 requires "INT8 AUROC drop <= 0.03", but every CirCor report so far is
FP32 PyTorch. The firmware runs INT8, so the number that matters for the
platform decision (ADR-006's switch gate) is the quantized one.

``train_cnn.py`` already exports ONNX and evaluates the static-INT8 model; this
script runs it once per frozen fold and aggregates the FP32-vs-INT8 comparison.
It shells out, so it stays torch-free and can run while other work uses the
Python environment.

Usage:
    python tools/ai/int8_cv.py --data-dir data/processed/circor \
        --splits-file data/processed/circor/splits.csv \
        --out data/reports/int8-cv.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRAIN = ROOT / "tools" / "ai" / "train_cnn.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--splits-file", required=True)
    parser.add_argument("--out", default="data/reports/int8-cv.json")
    parser.add_argument("--folds", default="0,1,2,3,4")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--window-selection", default="annotated",
                        choices=("all", "annotated"))
    parser.add_argument("--work-dir", default="data/reports/int8-cv")
    parser.add_argument("--reuse", action="store_true",
                        help="reuse an existing per-fold report.json instead of retraining")
    return parser.parse_args()


def run_fold(args: argparse.Namespace, fold: int) -> dict:
    out_dir = Path(args.work_dir) / f"fold{fold}"
    report_path = out_dir / "report.json"
    if not (args.reuse and report_path.exists()):
        out_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable, str(TRAIN),
            "--data-dir", args.data_dir,
            "--out-dir", str(out_dir),
            "--splits-file", args.splits_file,
            "--fold", str(fold),
            "--epochs", str(args.epochs),
            "--patience", str(args.patience),
            "--window-selection", args.window_selection,
        ]
        print(f"[int8] fold {fold}: {' '.join(command[-6:])}", flush=True)
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout).strip().splitlines()[-6:]
            raise SystemExit(f"fold {fold} failed:\n" + "\n".join(tail))
    if not report_path.exists():
        raise SystemExit(f"fold {fold}: no report at {report_path}")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    fp32 = report.get("evaluation", {}).get("patient_level", {})
    int8 = report.get("quantization", {}).get("int8_test_patient") or {}
    row = {
        "fold": fold,
        "fp32_auroc": fp32.get("auroc"),
        "int8_auroc": int8.get("auroc"),
        "fp32_balanced_accuracy": fp32.get("balanced_accuracy"),
        "int8_balanced_accuracy": int8.get("balanced_accuracy"),
        "threshold": fp32.get("threshold"),
        "int8_onnx_bytes": report.get("quantization", {}).get("int8_onnx_bytes"),
        "quantization_error": report.get("quantization", {}).get("quantization_error"),
        "report": str(report_path),
    }
    if row["fp32_auroc"] is not None and row["int8_auroc"] is not None:
        row["auroc_drop"] = row["fp32_auroc"] - row["int8_auroc"]
    print(
        f"[int8] fold {fold}: FP32 AUROC {row['fp32_auroc']}, "
        f"INT8 AUROC {row['int8_auroc']}, drop {row.get('auroc_drop')}",
        flush=True,
    )
    return row


def mean_std(values: list[float]) -> dict:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"mean": None, "std": None, "n": 0}
    return {
        "mean": statistics.fmean(clean),
        "std": statistics.stdev(clean) if len(clean) > 1 else 0.0,
        "n": len(clean),
    }


def main() -> None:
    args = parse_args()
    folds = [int(value) for value in args.folds.split(",") if value.strip()]
    rows = [run_fold(args, fold) for fold in folds]

    summary = {
        "fp32_auroc": mean_std([row["fp32_auroc"] for row in rows]),
        "int8_auroc": mean_std([row["int8_auroc"] for row in rows]),
        "fp32_balanced_accuracy": mean_std([row["fp32_balanced_accuracy"] for row in rows]),
        "int8_balanced_accuracy": mean_std([row["int8_balanced_accuracy"] for row in rows]),
        "auroc_drop": mean_std([row.get("auroc_drop") for row in rows]),
    }
    drop = summary["auroc_drop"]["mean"]
    # ADR-007 acceptance: an INT8 AUROC drop above 0.03 is a failure.
    verdict = (
        "ACCEPTABLE" if drop is not None and drop <= 0.03
        else "FAILS the ADR-007 0.03 drop criterion" if drop is not None
        else "UNMEASURED"
    )
    payload = {
        "protocol": {
            "data_dir": args.data_dir,
            "splits_file": args.splits_file,
            "epochs": args.epochs,
            "patience": args.patience,
            "window_selection": args.window_selection,
            "quantization": "onnxruntime static QOperator, per-channel QInt8 weights",
        },
        "per_fold": rows,
        "summary": summary,
        "verdict": verdict,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("\n=== FP32 vs INT8 (patient level, frozen folds) ===")
    print(f"FP32 AUROC {summary['fp32_auroc']['mean']:.4f} +- {summary['fp32_auroc']['std']:.4f}"
          if summary["fp32_auroc"]["mean"] is not None else "FP32 AUROC: unmeasured")
    print(f"INT8 AUROC {summary['int8_auroc']['mean']:.4f} +- {summary['int8_auroc']['std']:.4f}"
          if summary["int8_auroc"]["mean"] is not None else "INT8 AUROC: unmeasured")
    print(f"drop       {drop:.4f}" if drop is not None else "drop: unmeasured")
    print(f"ADR-007 verdict: {verdict}")
    print(f"[wrote] {out}")


if __name__ == "__main__":
    main()
