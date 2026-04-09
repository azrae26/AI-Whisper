---
name: build-and-package
description: 打包 AI Whisper 並壓縮成 zip 準備分發。當使用者說「包」、「打包」、「build」、「壓縮」、「產生 zip」、「分享給同事」時使用。
---

# AI Whisper 打包流程

使用專屬 venv（`.venv-pack`）打包，避免系統環境裡的 torch/scipy 等大型套件拖慢速度。
打包邏輯完整實作在 `deploy.ps1`，本文件說明使用方式與注意事項。

## 執行方式

### 只打包（不推 git）—— 說「包」時用這個

```powershell
powershell -ExecutionPolicy Bypass -File ".cursor/skills/build-and-package/pack.ps1"
```

Bash tool timeout：build 步驟 300000ms，zip 步驟 60000ms。

### 推 git + 並行打包

```powershell
powershell -ExecutionPolicy Bypass -File ".cursor/skills/build-and-package/deploy.ps1" -Role main -Message "commit msg"
```

## 完成後告知使用者

- zip 路徑：`dist/AI Whisper_yyyyMMdd_HHmm.zip`
- `dist/` 底下的 zip 只保留最近 3 個，其餘自動刪除
- 傳給同事，解壓後直接執行 `AI Whisper.exe`
- 首次執行需在設定頁輸入 API Key

## 注意事項

- 打包腳本路徑自動解析（`Split-Path -Parent $MyInvocation.MyCommand.Path`），不需手動改路徑
- 打包前自動備份 `dist/AI Whisper/config.json`，完成後還原，不會遺失使用者設定
- 壓縮時先複製到 TEMP 再 zip，避開專案目錄被防毒鎖
- `.venv-pack` 已列入 `.gitignore`，不會被推送；第一次執行 pack.ps1 時若不存在會自動建立
- 若打包失敗，先確認 `AI Whisper.exe` 程序已終止（pack.ps1 會自動處理）
