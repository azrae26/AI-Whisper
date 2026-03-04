---
name: push-and-package
description: 並行執行「推」與「包」，節省時間。TRIGGER: 使用者說「推 包」、「推包」、「推+包」時使用。
---

# 推 + 包 並行流程（deploy.ps1 腳本版）

使用 `deploy.ps1` 腳本，將 AI 工具調用從 9 次壓縮到 3 次，大幅縮短等待時間。

## 架構說明

`deploy.ps1` 支援 `-Role` 參數，自行啟動 build / zip 兩個獨立 PowerShell 進程，父腳本結束後它們仍繼續跑。

| 進程 | 角色 | 工作 |
|------|------|------|
| main | 前景 | 啟動 build/zip → git push |
| build | 背景獨立進程 | taskkill → 備份 config → PyInstaller |
| zip | 背景獨立進程 | 等舊 exe 消失 → 等新 exe 出現 → 還原 config → zip → 啟動 exe |

## AI 執行流程（3 步完成）

### Step 1：取得 diff（1 次工具調用）

```powershell
git -C "d:\Cursor\AI Whisper" status
git -C "d:\Cursor\AI Whisper" diff
```

- 同時看 untracked 檔案，判斷哪些需要加入（`-Files` 參數）
- 根據 diff 撰寫 commit message（不可臆測）

### Step 2：執行腳本（1 次工具調用）

```powershell
& "d:\Cursor\AI Whisper\deploy.ps1" -Message "commit message 內容" [-Files "新檔案1","新檔案2"]
```

- 腳本自動：add -u → add Files → commit → pull → push，並同時啟動背景打包
- 推完成後腳本輸出「推完成 (Xs)」，AI 即可回覆用戶

### Step 3：確認背景狀態（選擇性，1 次工具調用）

打包在背景進行，zip 完成後 exe 自動啟動。若需確認 zip 路徑，讀取背景進程的 terminal 輸出即可。

## -Files 參數說明

腳本預設只 `git add -u`（已追蹤的修改）。若有 untracked 新檔案需提交：

```powershell
& "d:\Cursor\AI Whisper\deploy.ps1" -Message "..." -Files ".cursor/rules/new-rule.mdc","assets/new-icon.png"
```

沒有 untracked 需要加時，省略 `-Files` 即可。

## 完成後告知

- **推**：腳本輸出「推完成 (Xs)」，AI 立即回覆
- **包**：背景繼續，zip 產於 `dist/AI Whisper_yyyyMMdd_HHmm.zip`，exe 自動啟動

## 注意事項

- `deploy.ps1` 已加入 `.gitignore` 排除清單（若不想推送的話）或直接提交
- 若想只推不包，直接用 git-push-workflow；只包不推，用 build-and-package
