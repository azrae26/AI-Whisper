---
name: build-and-package
description: 打包 AI Whisper 並壓縮成 zip 準備分發。當使用者說「包」、「打包」、「build」、「壓縮」、「產生 zip」、「分享給同事」時使用。
---

# AI Whisper 打包流程

使用專屬 venv（`.venv-pack`）打包，避免系統環境裡的 torch/scipy 等大型套件拖慢速度。

## 步驟

### 1. 終止舊程序
```powershell
taskkill /F /IM "AI Whisper.exe" 2>$null
Start-Sleep -Seconds 1
```

### 2. 建立／確認 venv（第一次需要，之後略過）
```powershell
$venv = "f:\Cursor\AI Whisper\.venv-pack"
if (-not (Test-Path $venv)) {
    py -m venv $venv
    & "$venv\Scripts\pip.exe" install -r "f:\Cursor\AI Whisper\requirements.txt" pyinstaller
}
```

### 3. 打包（使用 venv 的 python）
```powershell
$python = "f:\Cursor\AI Whisper\.venv-pack\Scripts\python.exe"
cd "f:\Cursor\AI Whisper"
& $python -m PyInstaller -y --onedir --windowed --icon=assets/icon.ico --name="AI Whisper" --add-data "assets;assets" --version-file version_info.txt --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import tkinter.messagebox --hidden-import _tkinter main.py
```

### 4. 壓縮成 zip
```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmm"
$zipName = "AI Whisper_$timestamp.zip"
Compress-Archive -Path "f:\Cursor\AI Whisper\dist\AI Whisper" -DestinationPath "f:\Cursor\AI Whisper\dist\$zipName" -Force
```

### 5. 啟動打包後的 exe
```powershell
Start-Process "f:\Cursor\AI Whisper\dist\AI Whisper\AI Whisper.exe"
```
執行時直接啟動 `dist\AI Whisper\AI Whisper.exe`，不要用 `py main.py`。

### 6. 完成後告知使用者
- zip 路徑：`dist/AI Whisper_yyyyMMdd_HHmm.zip`
- 傳給同事，解壓後直接執行 `AI Whisper.exe`
- 首次執行需在設定頁輸入 API Key

## 注意事項
- 使用 PowerShell，不使用 `&&`
- 打包指令已含 `--hidden-import tkinter` 等，解決 customtkinter 找不到 tkinter 的問題
- 若打包失敗，先檢查 exe 是否仍在執行（步驟 1）
- `.venv-pack` 已列入 `.gitignore`，不會被推送
- 路徑請依實際 workspace 路徑調整（非固定 f:\）
