#!/usr/bin/env bash
#
# Reproduce every number cited in the 2026-10-18 reports, in order.
#
# Everything is written under data/reports/reproduce-<UTC timestamp>/ so a run
# never overwrites the reports it is checking. data/ is gitignored.
#
# Requirements: the CirCor 1.0.3 dataset already extracted under
# data/raw/circor, and the project venv at .venv. Nothing is downloaded.
#
# Usage:
#   scripts/reproduce.sh              # everything that needs no hardware
#   scripts/reproduce.sh --fast       # skip the two CNN 5-fold runs (~15 min each)
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="data/reports/reproduce-${STAMP}"
PY="${ROOT}/.venv/bin/python"
DATA="data/processed/circor"
FAST=0
[[ "${1:-}" == "--fast" ]] && FAST=1

mkdir -p "${OUT}"
echo "==> output: ${OUT}"

have_data() { [[ -f "${DATA}/recordings.csv" && -f "${DATA}/splits.csv" ]]; }
have_venv() { [[ -x "${PY}" ]]; }

echo "==> toolchain"
ctest --test-dir build --output-on-failure | tee "${OUT}/ctest.txt"
npm test | tee "${OUT}/npm.txt"
"${PY}" -m unittest discover -s tests/python -v 2>&1 | tail -5 | tee "${OUT}/python.txt"

if ! have_venv; then
  echo "!! no venv at ${PY}; skipping data-dependent steps"; exit 0
fi
if ! have_data; then
  echo "!! no prepared dataset at ${DATA}; skipping data-dependent steps"
  echo "   run: ${PY} tools/ai/prepare_circor.py --extract-dir <extracted> --out-dir ${DATA}"
  exit 0
fi

echo "==> frozen split fingerprint (must be 1f212dea...)"
"${PY}" - <<'PY' | tee "${OUT}/fingerprint.txt"
import sys
sys.path.insert(0, "tools/ai")
from hs_splits import load_splits, splits_fingerprint
folds = load_splits("data/processed/circor/splits.csv")
print(f"patients={len(folds)} folds={sorted(set(folds.values()))}")
print(f"fingerprint={splits_fingerprint(folds)}")
PY

echo "==> S1/S2 event level (942 patients, annotation-masked)"
"${PY}" tools/ai/evaluate_s1s2.py --dataset "${DATA}" \
  --out "${OUT}/s1s2.json" | tee "${OUT}/s1s2.txt"

echo "==> heart rate vs annotation (3152 recordings)"
"${PY}" tools/ai/evaluate_heart_rate.py --dataset "${DATA}" \
  --out "${OUT}/heart-rate.json" | tee "${OUT}/heart-rate.txt"

echo "==> three-arm CV on the frozen splits (logreg, MFCC)"
"${PY}" tools/ai/cross_validate.py --data-dir "${DATA}" --model logreg \
  --window-selection annotated --splits-file "${DATA}/splits.csv" \
  --out-dir "${OUT}/cv-logreg" --dump-probabilities "${OUT}/prob-logreg.csv" \
  | tail -8 | tee "${OUT}/cv-logreg.txt"
"${PY}" tools/ai/train_mfcc.py --data-dir "${DATA}" \
  --window-selection annotated --splits-file "${DATA}/splits.csv" \
  --out-dir "${OUT}/cv-mfcc" --dump-probabilities "${OUT}/prob-mfcc.csv" \
  | tail -8 | tee "${OUT}/cv-mfcc.txt"

if [[ ${FAST} -eq 0 ]]; then
  echo "==> CNN 5-fold (~15 min on 8 cores)"
  OMP_NUM_THREADS=4 "${PY}" tools/ai/cross_validate.py --data-dir "${DATA}" --model cnn \
    --window-selection annotated --splits-file "${DATA}/splits.csv" \
    --out-dir "${OUT}/cv-cnn" --dump-probabilities "${OUT}/prob-cnn.csv" \
    --cnn-epochs 30 --cnn-patience 6 | tail -8 | tee "${OUT}/cv-cnn.txt"
else
  echo "==> --fast: skipping the CNN 5-fold"
fi

if [[ -f "${OUT}/prob-cnn.csv" ]]; then
  echo "==> calibration + workpoints"
  "${PY}" tools/ai/workpoint_analysis.py \
    --probs "logreg=${OUT}/prob-logreg.csv" "mfcc=${OUT}/prob-mfcc.csv" "cnn=${OUT}/prob-cnn.csv" \
    --out-dir "${OUT}" | tee "${OUT}/workpoint.txt"
else
  echo "==> workpoints skipped (no CNN probabilities)"
fi

echo
echo "==> done. Compare ${OUT} with the committed reports in data/reports/."
echo "    Numbers quoted in docs/ must cite the file that produced them."
