param(
    [int]$BatchSize = 50,
    [int]$RoundsPerRun = 1,
    [switch]$RetryFailed,
    [switch]$SkipReady,
    [int]$MinRows = 20,
    [string]$FailureCategory = "",
    [string]$ReportsDir = "data/reports",
    [string]$Pattern = "all_daily_kline_sync_report*.json",
    [string]$FailedSymbolsFile = "data/reports/all_daily_failed_symbols.json",
    [string]$ProgressFile = "data/reports/retry_failed_daily_progress.json",
    [string]$ReportBase = "data/reports/retry_failed_daily_report.json",
    [int]$SleepSecondsBetweenBatches = 3,
    [switch]$ResetProgress
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if ($ResetProgress -and (Test-Path $ProgressFile)) {
    Remove-Item -LiteralPath $ProgressFile -Force
}

python scripts/collect_failed_daily_symbols.py --reports-dir $ReportsDir --pattern $Pattern --output $FailedSymbolsFile
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if ($FailureCategory -ne "") {
    $filteredFile = [System.IO.Path]::ChangeExtension($FailedSymbolsFile, $null) + "_$FailureCategory.json"
    python -c "import json,sys; from pathlib import Path; data=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); cat=sys.argv[2]; syms=data.get('by_category',{}).get(cat,[]); Path(sys.argv[3]).write_text(json.dumps({'failed_symbols': syms}, ensure_ascii=False, indent=2), encoding='utf-8'); print(json.dumps({'category': cat, 'failed_count': len(syms), 'failed_symbols': syms}, ensure_ascii=False, indent=2))" $FailedSymbolsFile $FailureCategory $filteredFile
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    $FailedSymbolsFile = $filteredFile
}

if (-not (Test-Path $FailedSymbolsFile)) {
    Write-Host "failed symbols file not found: $FailedSymbolsFile" -ForegroundColor Yellow
    exit 0
}

$failedData = Get-Content $FailedSymbolsFile -Raw | ConvertFrom-Json
if ($null -eq $failedData.failed_symbols -or $failedData.failed_symbols.Count -eq 0) {
    if ($FailureCategory -ne "") {
        Write-Host "no failed symbols to retry for category [$FailureCategory]. nothing to run." -ForegroundColor Yellow
    } else {
        Write-Host "no failed symbols to retry. nothing to run." -ForegroundColor Yellow
    }
    exit 0
}

$logPath = "data/reports/retry_failed_daily_klines.log"
"[$(Get-Date -Format s)] start retry failed runner" | Out-File -FilePath $logPath -Append -Encoding utf8
$finalStillFailedFile = "data/reports/retry_failed_daily_still_failed.json"

$currentBatchIndex = 0
if ((Test-Path $ProgressFile) -and (-not $ResetProgress)) {
    try {
        $progress = Get-Content $ProgressFile -Raw | ConvertFrom-Json
        if (($null -ne $progress.completed) -and [bool]$progress.completed -eq $true) {
            Write-Host "all retry batches already completed. Use -ResetProgress to rerun." -ForegroundColor Yellow
        }
        if ($null -ne $progress.next_batch_index) {
            $currentBatchIndex = [int]$progress.next_batch_index
        }
    }
    catch {
        $currentBatchIndex = 0
    }
}

while ($true) {
    $cmd = @(
        "python",
        "scripts/sync_all_daily_klines.py",
        "--retry-from-report", $FailedSymbolsFile,
        "--batch-size", $BatchSize,
        "--batch-index", $currentBatchIndex,
        "--rounds", $RoundsPerRun,
        "--report", $ReportBase,
        "--progress-file", $ProgressFile,
        "--min-rows", $MinRows
    )

    if ($RetryFailed) { $cmd += "--retry-failed" }
    if ($SkipReady) { $cmd += "--skip-ready" }

    "[$(Get-Date -Format s)] running retry batch_index=$currentBatchIndex command=$($cmd -join ' ')" | Out-File -FilePath $logPath -Append -Encoding utf8
    & $cmd[0] $cmd[1..($cmd.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        "[$(Get-Date -Format s)] retry command failed with exit code $LASTEXITCODE" | Out-File -FilePath $logPath -Append -Encoding utf8
        exit $LASTEXITCODE
    }

    if (-not (Test-Path $ProgressFile)) {
        break
    }

    $progress = Get-Content $ProgressFile -Raw | ConvertFrom-Json
    $hasMore = $true
    if ($null -ne $progress.has_more_batches) {
        $hasMore = [bool]$progress.has_more_batches
    }
    if ($null -ne $progress.next_batch_index) {
        $currentBatchIndex = [int]$progress.next_batch_index
    }

    "[$(Get-Date -Format s)] completed retry batch, next_batch_index=$currentBatchIndex has_more_batches=$hasMore" | Out-File -FilePath $logPath -Append -Encoding utf8

    if (-not $hasMore) {
        python scripts/collect_failed_daily_symbols.py --reports-dir data/reports --pattern "retry_failed_daily_report*.json" --output $finalStillFailedFile
        "[$(Get-Date -Format s)] all retry batches completed" | Out-File -FilePath $logPath -Append -Encoding utf8
        break
    }

    Start-Sleep -Seconds $SleepSecondsBetweenBatches
}
