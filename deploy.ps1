# 功能：並行執行 git push 與 PyInstaller 打包
# 職責：-Role main 同時啟動 build/zip 背景進程並執行推送；-Role build 執行 PyInstaller；-Role zip 等待建置完成後壓縮
# 依賴：.venv-pack/（含 PyInstaller 與 customtkinter）、git

param(
    [string]  $Role    = "main",
    [string]  $Message = "",
    [string[]]$Files   = @()
)

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$self      = $MyInvocation.MyCommand.Path
$python    = "$workspace\.venv-pack\Scripts\python.exe"
$ctkPath   = "$workspace\.venv-pack\Lib\site-packages\customtkinter"
$distDir   = "$workspace\dist\AI Whisper"
$configBak = "$workspace\config.json.pack.bak"

switch ($Role) {

    "main" {
        # 同時起跑 build 和 zip（獨立 PowerShell 進程，父腳本結束後仍繼續）
        Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$self`" -Role build"
        Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$self`" -Role zip"

        # 前景執行推
        $t0 = Get-Date
        git -C $workspace add -u
        foreach ($f in $Files) { git -C $workspace add "$f" }
        git -C $workspace commit -m $Message
        git -C $workspace pull origin master
        git -C $workspace push origin master
        $sec = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
        Write-Host "推完成 ($sec`s)，包在背景繼續中"
    }

    "build" {
        taskkill /F /IM "AI Whisper.exe" 2>$null
        taskkill /F /IM python.exe 2>$null
        if (Test-Path "$distDir\config.json") { Copy-Item "$distDir\config.json" $configBak -Force }
        Set-Location $workspace
        & $python -m PyInstaller -y --onedir --windowed `
            --icon=assets/icon.ico --name="AI Whisper" `
            --add-data "assets;assets" --add-data "$ctkPath;customtkinter" `
            --version-file version_info.txt `
            --hidden-import tkinter --hidden-import tkinter.ttk `
            --hidden-import tkinter.messagebox --hidden-import _tkinter `
            main.py
    }

    "zip" {
        # 等 PyInstaller 清空 dist（舊 exe 消失代表建置已開始）
        $gone = 60; $e = 0
        while ((Test-Path "$distDir\AI Whisper.exe") -and $e -lt $gone) {
            Start-Sleep -Seconds 1; $e++
        }
        # 等新 exe 出現
        $maxWait = 120; $elapsed = 0
        while (-not (Test-Path "$distDir\AI Whisper.exe") -and $elapsed -lt $maxWait) {
            Start-Sleep -Seconds 2; $elapsed += 2
        }
        if (Test-Path "$distDir\AI Whisper.exe") {
            if (Test-Path $configBak) {
                Copy-Item $configBak "$distDir\config.json" -Force
                Remove-Item $configBak -Force
            }
            $timestamp     = Get-Date -Format "yyyyMMdd_HHmm"
            $zipName       = "AI Whisper_$timestamp.zip"
            $stagingParent = "$env:TEMP\AI_Whisper_zipstaging"
            $stagingDir    = "$stagingParent\AI Whisper"
            if (Test-Path $stagingParent) { Remove-Item $stagingParent -Recurse -Force }
            New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
            Copy-Item "$distDir\*" $stagingDir -Recurse -Force
            Compress-Archive -Path $stagingDir -DestinationPath "$workspace\dist\$zipName" -Force
            Remove-Item $stagingParent -Recurse -Force -ErrorAction SilentlyContinue
            # zip 只保留最近 3 個，其餘刪除
            Get-ChildItem "$workspace\dist\AI Whisper_*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 | Remove-Item -Force
            Start-Process "$distDir\AI Whisper.exe"
            Write-Host "zip: dist\$zipName"
        } else {
            Write-Host "Build timed out or failed"
        }
    }
}
