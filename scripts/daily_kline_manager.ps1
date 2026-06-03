param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Pause-And-Return {
    Write-Host ""
    Read-Host "按回车返回菜单 / Press Enter to return"
}

function Show-FileOrMessage {
    param(
        [string]$Path,
        [string]$MissingMessage
    )
    if (Test-Path $Path) {
        Get-Content $Path
    }
    else {
        Write-Host $MissingMessage -ForegroundColor Yellow
    }
}

while ($true) {
    Clear-Host
    Write-Host "============================================"
    Write-Host "  日线K线同步管理器 / Daily Kline Sync Manager"
    Write-Host "============================================"
    Write-Host "1. 继续跑下一批 / Continue next market batch"
    Write-Host "2. 从头重跑全量 / Restart all market batches from beginning"
    Write-Host "3. 汇总失败报告 / Collect failed symbol reports"
    Write-Host "4. 重跑全部失败股票 / Retry all failed symbols"
    Write-Host "5. 重跑网络类失败 / Retry network-related failed symbols"
    Write-Host "6. 重跑远端断开失败 / Retry remote-disconnect failed symbols"
    Write-Host "7. 重跑连接建立失败 / Retry connection-establish failed symbols"
    Write-Host "8. 重跑空数据失败 / Retry empty-data failed symbols"
    Write-Host "9. 清理 daily/minute 缓存 / Cleanup daily-minute cache files"
    Write-Host "10. 同步 stock_basic 基础表 / Sync stock_basic table"
    Write-Host "11. 查看进度文件 / Show progress file"
    Write-Host "12. 查看失败汇总 / Show failed summary file"
    Write-Host "0. 退出 / Exit"
    Write-Host "============================================"

    $choice = Read-Host "请输入编号 / Enter option number"

    switch ($choice) {
        "1" {
            & "$PSScriptRoot\run_all_daily_klines_batches.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20
            Pause-And-Return
        }
        "2" {
            & "$PSScriptRoot\run_all_daily_klines_batches.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -ResetProgress
            Pause-And-Return
        }
        "3" {
            python "$PSScriptRoot\collect_failed_daily_symbols.py"
            Pause-And-Return
        }
        "4" {
            & "$PSScriptRoot\retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20
            Pause-And-Return
        }
        "5" {
            & "$PSScriptRoot\retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -FailureCategory "network_proxy"
            & "$PSScriptRoot\retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -FailureCategory "network_connect"
            & "$PSScriptRoot\retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -FailureCategory "network_timeout"
            & "$PSScriptRoot\retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -FailureCategory "network_remote_disconnect"
            Pause-And-Return
        }
        "6" {
            & "$PSScriptRoot\retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -FailureCategory "network_remote_disconnect"
            Pause-And-Return
        }
        "7" {
            & "$PSScriptRoot\retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -FailureCategory "network_connect"
            Pause-And-Return
        }
        "8" {
            & "$PSScriptRoot\retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -FailureCategory "empty_data"
            Pause-And-Return
        }
        "9" {
            python "$PSScriptRoot\cleanup_market_data_cache.py"
            Pause-And-Return
        }
        "10" {
            python "$PSScriptRoot\sync_stock_basic.py"
            Pause-And-Return
        }
        "11" {
            Show-FileOrMessage -Path "data/reports/all_daily_kline_sync_progress.json" -MissingMessage "未找到进度文件 / Progress file not found."
            Pause-And-Return
        }
        "12" {
            Show-FileOrMessage -Path "data/reports/all_daily_failed_symbols.json" -MissingMessage "未找到失败汇总文件 / Failed summary file not found."
            Pause-And-Return
        }
        "0" {
            break
        }
        default {
            Write-Host "无效选项 / Invalid option" -ForegroundColor Yellow
            Pause-And-Return
        }
    }
}
