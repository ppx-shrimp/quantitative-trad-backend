@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
if /I "%1"=="reset" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_all_daily_klines_batches.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -ResetProgress
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_all_daily_klines_batches.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20
)

endlocal
