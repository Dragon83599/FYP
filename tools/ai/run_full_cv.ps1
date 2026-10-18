# Run the full patient-level 5-fold cross-validation matrix on CirCor.
#
# Every cell of the matrix must run on the same frozen patient folds, otherwise
# the "same 5-fold protocol" claim is not verifiable across models/selection
# variants. Pass -SplitsFile to make every cross_validate.py cell cite the frozen
# file (its sha256 fingerprint is printed here and recorded in each report.json).
#
# Default behaviour without -SplitsFile: the folds are re-derived from the
# cross_validate.py --seed default, exactly as before frozen split files existed.
#
#   .\run_full_cv.ps1
#   .\run_full_cv.ps1 -SplitsFile data/processed/circor/splits.csv
#   .\run_full_cv.ps1 -SplitsFile data/processed/circor/splits.csv -Fold 0
#
# -Fold N additionally trains the single-model CNN (train_cnn.py) with fold N as
# the test set and the same frozen validation carve, into
# data/reports/circor-cnn-foldN. It needs -SplitsFile: a single fold is only
# meaningful relative to a frozen file.
param(
    [string]$SplitsFile = '',
    [int]$Fold = -1
)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
$systemPython = 'C:\Users\psr\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$venvPython = '.\.venv\Scripts\python.exe'
$env:PYTHONIOENCODING = 'utf-8'

$splitArgs = @()
$resolvedSplits = ''
if ($SplitsFile -ne '') {
    if (-not (Test-Path -LiteralPath $SplitsFile -PathType Leaf)) {
        throw "-SplitsFile '$SplitsFile' not found; run tools/ai/freeze_splits.py first"
    }
    # Convert-Path, not Resolve-Path: a PathInfo stringifies to
    # 'Microsoft.PowerShell.Core\FileSystem::<path>' (always for UNC paths, which
    # is what this repo looks like when the script is run from WSL), and Python
    # cannot open that. Convert-Path returns a plain string.
    $resolvedSplits = (Convert-Path -LiteralPath $SplitsFile)
    $splitArgs = @('--splits-file', $resolvedSplits)

    # Cite the fingerprint before any model runs, so the log proves which split
    # the four cells below shared.
    $fingerprintMatch = Select-String -LiteralPath $resolvedSplits -Pattern '^#\s*fingerprint:\s*(\S+)\s*$' |
        Select-Object -First 1
    if ($null -eq $fingerprintMatch) {
        Write-Host "[full-cv] frozen splits: $resolvedSplits (no '# fingerprint:' line in the file)"
    } else {
        $fingerprint = $fingerprintMatch.Matches[0].Groups[1].Value
        Write-Host "[full-cv] frozen splits: $resolvedSplits (sha256 $fingerprint)"
    }

    # A frozen protocol cannot be re-cut: cross_validate.py rejects a file whose
    # fold count differs from --folds, so fail here with the same reason instead
    # of after four long CV runs.
    $nFoldsMatch = Select-String -LiteralPath $resolvedSplits -Pattern '^#\s*n_folds:\s*(\d+)\s*$' |
        Select-Object -First 1
    if (($null -ne $nFoldsMatch) -and ([int]$nFoldsMatch.Matches[0].Groups[1].Value -ne 5)) {
        throw ("$resolvedSplits defines $($nFoldsMatch.Matches[0].Groups[1].Value) folds " +
            "but this matrix is a 5-fold run; freeze a 5-fold file or change --folds here")
    }
} elseif ($Fold -ge 0) {
    throw '-Fold needs -SplitsFile: a single fold is only meaningful with a frozen split file'
}

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
        --cnn-patience 6 `
        @splitArgs
}

if ($Fold -ge 0) {
    $foldOutput = "circor-cnn-fold$Fold"
    Write-Host "=== $foldOutput (single fold $Fold of the frozen file) ==="
    & $venvPython -B tools/ai/train_cnn.py `
        --data-dir data/processed/circor `
        --out-dir "data/reports/$foldOutput" `
        --epochs 30 `
        --patience 6 `
        --splits-file $resolvedSplits `
        --fold $Fold
    if ($LASTEXITCODE -ne 0) {
        throw "train_cnn.py failed for fold $Fold (exit $LASTEXITCODE); see the log above"
    }
}

Write-Host '[full-cv] all configurations finished'
