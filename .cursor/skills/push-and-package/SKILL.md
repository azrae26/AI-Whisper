---
name: push-and-package
description: 並行執行「推」與「包」，節省時間。TRIGGER: 使用者說「推 包」、「推包」、「推+包」時使用。
---

# 推 + 包 並行流程（優化版）

三線並行：打包建置、推、打包收尾同時跑。推完成即回覆，包在背景跑完。

## 執行順序（t=0 同時啟動三項）

| 線程 | 類型 | 工作 |
|------|------|------|
| Step 1 | 背景 | taskkill → 備份 config → PyInstaller 建置 |
| Step 2 | 前景 | git diff → add → commit → pull → push |
| Step 3 | 背景 | 輪詢 exe（間隔 2s）→ 還原 config → zip → 啟動 exe |

Step 2 完成即可回覆用戶「推完成」；Step 3 在背景完成後會產 zip 並啟動 exe，可讀其 terminal 取得 zip 路徑。

## Step 1：背景啟動打包（is_background: true）

```powershell
taskkill /F /IM "AI Whisper.exe" 2>$null; Start-Sleep -Seconds 1
$workspace = "<workspace>"  # 依實際路徑，如 d:\Cursor\AI Whisper
$configBak = "$workspace\config.json.pack.bak"
$distDir = "$workspace\dist\AI Whisper"
if (Test-Path "$distDir\config.json") { Copy-Item "$distDir\config.json" $configBak -Force }
$python = "$workspace\.venv-pack\Scripts\python.exe"
$ctkPath = "$workspace\.venv-pack\Lib\site-packages\customtkinter"
Set-Location $workspace
& $python -m PyInstaller -y --onedir --windowed --icon=assets/icon.ico --name="AI Whisper" --add-data "assets;assets" --add-data "$ctkPath;customtkinter" --version-file version_info.txt --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import tkinter.messagebox --hidden-import _tkinter main.py
```

## Step 2：前景執行推（依 git-push-workflow）

1. `git status`、`git diff`、`git diff --cached` 取得變更
2. 撰寫 commit_msg.txt（依 diff 內容，不可臆測）
3. `git add` 需要提交的檔案
4. `git commit -F commit_msg.txt`
5. `git pull origin <branch>`、`git push origin <branch>`
6. 刪除 commit_msg.txt

## Step 3：背景等待打包並收尾（is_background: true，timeout 180000）

與 Step 1、Step 2 同時啟動。輪詢間隔 2s 以加快偵測建置完成。

```powershell
$workspace = "<workspace>"
$maxWait = 120
$elapsed = 0
while (-not (Test-Path "$workspace\dist\AI Whisper\AI Whisper.exe") -and $elapsed -lt $maxWait) {
  Start-Sleep -Seconds 2
  $elapsed += 2
}
if (Test-Path "$workspace\dist\AI Whisper\AI Whisper.exe") {
  $configBak = "$workspace\config.json.pack.bak"
  $distDir = "$workspace\dist\AI Whisper"
  if (Test-Path $configBak) { Copy-Item $configBak "$distDir\config.json" -Force; Remove-Item $configBak -Force }
  Start-Sleep -Seconds 2
  $timestamp = Get-Date -Format "yyyyMMdd_HHmm"
  $zipName = "AI Whisper_$timestamp.zip"
  $srcDir = "$workspace\dist\AI Whisper"
  $stagingParent = "$env:TEMP\AI_Whisper_zipstaging"
  $stagingDir = "$stagingParent\AI Whisper"
  if (Test-Path $stagingParent) { Remove-Item $stagingParent -Recurse -Force }
  New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
  Copy-Item "$srcDir\*" $stagingDir -Recurse -Force
  Start-Sleep -Seconds 5
  Compress-Archive -Path $stagingDir -DestinationPath "$workspace\dist\$zipName" -Force
  Remove-Item $stagingParent -Recurse -Force -ErrorAction SilentlyContinue
  Start-Process "$workspace\dist\AI Whisper\AI Whisper.exe"
  Write-Host "zip: dist\$zipName"
} else { Write-Host "Build timed out or failed" }
```

## 完成後告知

- **推**：已 push 至 origin（Step 2 完成即回覆）
- **包**：在背景執行，完成後 zip 產於 `dist/`、exe 會自動啟動；可讀 Step 3 的 terminal 輸出取得 zip 檔名

## 優化摘要

| 優化 | 說明 |
|------|------|
| Step 3 改背景 | 不阻塞回覆；推完成即可回覆用戶 |
| 輪詢 5s→2s | 建置完成後約快 0–3s 偵測到 |
| 三線同時起跑 | Step 3 的等待與 Step 1 並行，不再等 Step 2 |

## 注意事項

- 路徑 `$workspace` 需依實際 workspace 調整（如 `d:\Cursor\AI Whisper`）
- Step 1、2、3 同時啟動；Step 2 為唯一前景阻塞，完成即可回覆
