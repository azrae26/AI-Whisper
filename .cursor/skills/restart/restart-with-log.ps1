# 功能：重啟 AI Whisper 並將 stdout/stderr 寫入時間戳 log 檔
# 職責：終止舊程序、啟動 main.py、產生 ai_whisper_yyyyMMdd_HHmmss.log

param(
    [switch]$NoKill  # 若有 -NoKill 則不先終止 python/AI Whisper
)

$ErrorActionPreference = 'SilentlyContinue'
$workspace = "f:\Cursor\AI Whisper"

Set-Location $workspace

if (-not $NoKill) {
    taskkill /F /IM "AI Whisper.exe" 2>$null | Out-Null
    taskkill /F /IM python.exe 2>$null | Out-Null
    Start-Sleep -Seconds 2
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$logName = "ai_whisper_$ts.log"
$logFull = Join-Path $workspace $logName

Write-Host "[$ts] Restart AI Whisper, LOG -> $logFull"
$env:PYTHONUNBUFFERED = "1"
Start-Process cmd -ArgumentList "/c", "py -u main.py > `"$logFull`" 2>&1" -WorkingDirectory $workspace -WindowStyle Hidden

Start-Sleep -Seconds 4
if (Test-Path $logFull) {
    Write-Host "--- LOG (last 15 lines) ---"
    Get-Content $logFull -Tail 15 -Encoding UTF8
}
Write-Host "`nLOG: $logFull"
