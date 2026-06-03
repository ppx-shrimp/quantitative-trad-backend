param(
    [int]$BatchSize = 50,
    [int]$StartBatchIndex = 0,
    [int]$RoundsPerRun = 1,
    [switch]$RetryFailed,
    [switch]$SkipReady,
    [int]$MinRows = 20,
    [string]$ProgressFile = "data/reports/all_daily_kline_sync_progress.json",
    [string]$ReportBase = "data/reports/all_daily_kline_sync_report.json",
    [int]$SleepSecondsBetweenBatches = 3,
    [switch]$ResetProgress
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if ($ResetProgress -and (Test-Path $ProgressFile)) {
    Remove-Item -LiteralPath $ProgressFile -Force
}

if (-not (Test-Path "data/reports")) {
    New-Item -ItemType Directory -Path "data/reports" | Out-Null
}

$logPath = "data/reports/run_all_daily_klines_batches.log"
"[$(Get-Date -Format s)] start batch runner" | Out-File -FilePath $logPath -Append -Encoding utf8

$currentBatchIndex = $StartBatchIndex
if ((Test-Path $ProgressFile) -and (-not $ResetProgress)) {
    try {
        $progress = Get-Content $ProgressFile -Raw | ConvertFrom-Json
        if (($null -ne $progress.completed) -and [bool]$progress.completed -eq $true) {
            Write-Host "all batches already completed. Use -ResetProgress to rerun from the beginning." -ForegroundColor Yellow
        }
        if ($null -ne $progress.next_batch_index) {
            $currentBatchIndex = [int]$progress.next_batch_index
        }
    }
    catch {
        "[$(Get-Date -Format s)] failed to parse progress file, fallback to StartBatchIndex=$StartBatchIndex" | Out-File -FilePath $logPath -Append -Encoding utf8
        $currentBatchIndex = $StartBatchIndex
    }
}

while ($true) {
    $cmd = @(
        "python",
        "scripts/sync_all_daily_klines.py",
        "--batch-size", $BatchSize,
        "--batch-index", $currentBatchIndex,
        "--rounds", $RoundsPerRun,
        "--report", $ReportBase,
        "--progress-file", $ProgressFile,
        "--min-rows", $MinRows
    )

    if ($RetryFailed) { $cmd += "--retry-failed" }
    if ($SkipReady) { $cmd += "--skip-ready" }

    "[$(Get-Date -Format s)] running batch_index=$currentBatchIndex command=$($cmd -join ' ')" | Out-File -FilePath $logPath -Append -Encoding utf8
    & $cmd[0] $cmd[1..($cmd.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        "[$(Get-Date -Format s)] sync command failed with exit code $LASTEXITCODE" | Out-File -FilePath $logPath -Append -Encoding utf8
        exit $LASTEXITCODE
    }

    if (-not (Test-Path $ProgressFile)) {
        "[$(Get-Date -Format s)] progress file missing after batch run, stop" | Out-File -FilePath $logPath -Append -Encoding utf8
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

    "[$(Get-Date -Format s)] completed batch, next_batch_index=$currentBatchIndex has_more_batches=$hasMore" | Out-File -FilePath $logPath -Append -Encoding utf8

    if (-not $hasMore) {
        "[$(Get-Date -Format s)] all batches completed" | Out-File -FilePath $logPath -Append -Encoding utf8
        break
    }

    Start-Sleep -Seconds $SleepSecondsBetweenBatches
}
