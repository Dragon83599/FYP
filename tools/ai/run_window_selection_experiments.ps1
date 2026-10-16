# Reproduce the all-windows vs annotated-windows comparison on the CirCor subset.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
$systemPython = 'C:\Users\psr\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$venvPython = '.\.venv\Scripts\python.exe'
$env:PYTHONIOENCODING = 'utf-8'

Write-Host '[experiment] refresh manifest with segmentation paths'
& $systemPython -B tools/ai/prepare_circor.py `
    --extract-dir data/raw/circor/subset `
    --out-dir data/processed/circor-subset

Write-Host '[experiment] logistic regression, all windows'
& $systemPython -B tools/ai/train_baseline.py `
    --data-dir data/processed/circor-subset `
    --cache data/processed/circor-subset/features-all.npz `
    --out-dir data/reports/circor-subset-logreg-all `
    --window-selection all `
    --rebuild-cache

Write-Host '[experiment] logistic regression, annotated windows only'
& $systemPython -B tools/ai/train_baseline.py `
    --data-dir data/processed/circor-subset `
    --cache data/processed/circor-subset/features-annotated.npz `
    --out-dir data/reports/circor-subset-logreg-annotated `
    --window-selection annotated `
    --min-annotated-fraction 0.5 `
    --rebuild-cache

Write-Host '[experiment] CNN, all windows'
& $venvPython -B tools/ai/train_cnn.py `
    --data-dir data/processed/circor-subset `
    --out-dir data/reports/circor-subset-cnn-all `
    --window-selection all `
    --max-windows-per-record 0 `
    --rebuild-cache

Write-Host '[experiment] CNN, annotated windows only'
& $venvPython -B tools/ai/train_cnn.py `
    --data-dir data/processed/circor-subset `
    --out-dir data/reports/circor-subset-cnn-annotated `
    --window-selection annotated `
    --min-annotated-fraction 0.5 `
    --max-windows-per-record 0 `
    --rebuild-cache

Write-Host '[experiment] done'
