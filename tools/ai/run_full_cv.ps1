# Run the full patient-level 5-fold cross-validation matrix on CirCor.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
$systemPython = 'C:\Users\psr\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$venvPython = '.\.venv\Scripts\python.exe'
$env:PYTHONIOENCODING = 'utf-8'

$configs = @(
    @{ Model = 'logreg'; Selection = 'all'; Output = 'circor-cv-logreg-all' },
    @{ Model = 'logreg'; Selection = 'annotated'; Output = 'circor-cv-logreg-annotated' },
    @{ Model = 'cnn'; Selection = 'all'; Output = 'circor-cv-cnn-all' },
    @{ Model = 'cnn'; Selection = 'annotated'; Output = 'circor-cv-cnn-annotated' }
)

foreach ($config in $configs) {
    $python = if ($config.Model -eq 'logreg') { $systemPython } else { $venvPython }
    Write-Host "=== $($config.Output) ==="
    & $python -B tools/ai/cross_validate.py `
        --data-dir data/processed/circor `
        --out-dir "data/reports/$($config.Output)" `
        --model $config.Model `
        --folds 5 `
        --max-windows-per-record 4 `
        --window-selection $config.Selection `
        --cnn-epochs 30 `
        --cnn-patience 6
}

Write-Host '[full-cv] all configurations finished'
