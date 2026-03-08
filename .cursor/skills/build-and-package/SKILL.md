---
name: build-and-package
description: 打包 AI Whisper 並壓縮成 zip 準備分發。當使用者說「包」、「打包」、「build」、「壓縮」、「產生 zip」、「分享給同事」時使用。
---

# AI Whisper 打包流程

使用專屬 venv（`.venv-pack`）打包，避免系統環境裡的 torch/scipy 等大型套件拖慢速度。

## 步驟

### 1. 終止舊程序（含開發版 python）
```powershell
taskkill /F /IM "AI Whisper.exe" 2>$null
taskkill /F /IM python.exe 2>$null
Start-Sleep -Seconds 1
```

### 2. 備份 config.json（PyInstaller 會清空 dist，先備份避免設定遺失）
```powershell
$distDir = "f:\Cursor\AI Whisper\dist\AI Whisper"
$configBak = "f:\Cursor\AI Whisper\config.json.pack.bak"
if (Test-Path "$distDir\config.json") { Copy-Item "$distDir\config.json" $configBak -Force }
```

### 3. 建立／確認 venv（第一次需要，之後略過）
```powershell
$venv = "f:\Cursor\AI Whisper\.venv-pack"
if (-not (Test-Path $venv)) {
    py -m venv $venv
    & "$venv\Scripts\pip.exe" install -r "f:\Cursor\AI Whisper\requirements.txt" pyinstaller
}
```

### 4. 打包（使用 venv 的 python）
```powershell
$workspace = "f:\Cursor\AI Whisper"
$python = "$workspace\.venv-pack\Scripts\python.exe"
$ctkPath = "$workspace\.venv-pack\Lib\site-packages\customtkinter"
cd $workspace
$tcl = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\tcl\tcl8.6"
$tk  = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\tcl\tk8.6"
& $python -m PyInstaller -y --onedir --windowed --icon=assets/icon.ico --name="AI Whisper" --add-data "assets;assets" --add-data "$ctkPath;customtkinter" --add-data "${tcl};_tcl_data" --add-data "${tk};_tk_data" --version-file version_info.txt --collect-all tkinter --hidden-import _tkinter main.py
```
CustomTkinter 含 .json 等資料檔，PyInstaller 不會自動打包，需手動 `--add-data`（參見 [CustomTkinter Packaging](https://github.com/TomSchimansky/CustomTkinter/wiki/Packaging#windows-pyinstaller-auto-py-to-exe)）。

### 5. 還原 config.json
```powershell
$configBak = "f:\Cursor\AI Whisper\config.json.pack.bak"
$distDir = "f:\Cursor\AI Whisper\dist\AI Whisper"
if (Test-Path $configBak) { Copy-Item $configBak "$distDir\config.json" -Force; Remove-Item $configBak -Force }
```

### 6. 壓縮成 zip（複製到 TEMP 再壓縮，避開專案目錄被防毒鎖）
```powershell
Start-Sleep -Seconds 2
$workspace = "f:\Cursor\AI Whisper"
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
# zip 只保留最近 3 個，其餘刪除
Get-ChildItem "$workspace\dist\AI Whisper_*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 | Remove-Item -Force
```

### 7. 啟動打包後的 exe
```powershell
Start-Process "f:\Cursor\AI Whisper\dist\AI Whisper\AI Whisper.exe"
```
執行時直接啟動 `dist\AI Whisper\AI Whisper.exe`，不要用 `py main.py`。

### 8. 完成後告知使用者
- zip 路徑：`dist/AI Whisper_yyyyMMdd_HHmm.zip`
- `dist/` 底下的 zip 只保留最近 3 個，其餘自動刪除
- 傳給同事，解壓後直接執行 `AI Whisper.exe`
- 首次執行需在設定頁輸入 API Key

## 注意事項
- 使用 PowerShell，不使用 `&&`
- 打包指令已含 `--hidden-import tkinter` 等、`--add-data` customtkinter（含 themes/*.json），解決 customtkinter 找不到 tkinter 與 theme 的問題
- 若打包失敗，先檢查 exe 是否仍在執行（步驟 1）
- `.venv-pack` 已列入 `.gitignore`，不會被推送
- 路徑請依實際 workspace 路徑調整（非固定 f:\）
- 步驟 2 備份、步驟 5 還原 config.json，打包時不刪除使用者設定
