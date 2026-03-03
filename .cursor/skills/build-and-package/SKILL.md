---
name: build-and-package
description: 打包 AI Whisper 並壓縮成 zip 準備分發。當使用者說「包」、「打包」、「build」、「壓縮」、「產生 zip」、「分享給同事」時使用。
---

# AI Whisper 打包流程

## 步驟

### 1. 終止舊程序
```powershell
taskkill /F /IM "AI Whisper.exe" 2>$null
Start-Sleep -Seconds 1
```

### 2. 打包
```powershell
cd "d:\Cursor\AI Whisper"
python -m PyInstaller -y --onedir --windowed --icon=assets/icon.ico --name="AI Whisper" --add-data "assets;assets" --version-file version_info.txt main.py
```

### 3. 壓縮成 zip
```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmm"
$zipName = "AI Whisper_$timestamp.zip"
Compress-Archive -Path "d:\Cursor\AI Whisper\dist\AI Whisper" -DestinationPath "d:\Cursor\AI Whisper\dist\$zipName" -Force
```

### 4. 完成後告知使用者
- zip 路徑：`dist/AI Whisper_yyyyMMdd_HHmm.zip`
- 傳給同事，解壓後直接執行 `AI Whisper.exe`
- 首次執行需在設定頁輸入 API Key

## 注意事項
- 使用 PowerShell，不使用 `&&`
- 打包指令固定，不要修改參數
- 若打包失敗，先檢查 exe 是否仍在執行（步驟 1）
