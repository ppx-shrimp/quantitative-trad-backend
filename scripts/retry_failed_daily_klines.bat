@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "CATEGORY=%2"

if /I "%1"=="reset" (
  if not "%CATEGORY%"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -FailureCategory "%CATEGORY%" -ResetProgress
  ) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -ResetProgress
  )
) else (
  if not "%1"=="" if /I not "%1"=="reset" set "CATEGORY=%1"
  if not "%CATEGORY%"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20 -FailureCategory "%CATEGORY%"
  ) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%retry_failed_daily_klines.ps1" -BatchSize 50 -RetryFailed -SkipReady -MinRows 20
  )
)

endlocal
