# AI Whisper - 語音轉文字

按一下快捷鍵開始錄音，再按一下停止，辨識結果自動貼到游標處。

## 使用方法

### 開發模式
```bash
python main.py
```

### 首次使用
1. 啟動後自動開啟設定頁面
2. 輸入 OpenAI API Key（格式：`sk-...`）
3. 設定完成後按「儲存設定」

## 快捷鍵
- **錄音**：預設 `Pause`（鍵盤上方暫停鍵，可在設定中自訂）
  - 第一次按：開始錄音
  - 第二次按：停止並辨識
  - 辨識完成後文字自動貼到當前游標位置
- **貼上歷史**：預設 `Alt+Shift+1`～`Alt+Shift+5`，可於設定頁自訂
  - 1 = 最新一句、2 = 第二新、…、5 = 最舊第五句

> **注意：** `keyboard` 套件在 Windows 需要以系統管理員身份執行，全域快捷鍵才能在所有應用程式中作用。

## 設定項目

| 項目 | 說明 |
|------|------|
| API Key | OpenAI API Key，存在本機 config.json |
| 辨識模型 | `gpt-4o-transcribe`（最強）/ `whisper-1`（舊版相容） |
| 全域快捷鍵 | 錄音與貼上歷史 1~5 句皆可自訂，格式如 `pause`、`alt+shift+1` |
| 開機啟動 | 寫入 Windows 登錄機碼自動啟動 |

## 打包成 .exe

```bash
python -m pip install pyinstaller
pyinstaller --onefile --windowed --icon=assets/icon.png --name=AIWhisper main.py
```

產出位於 `dist/AIWhisper.exe`，分享時附上 `assets/` 資料夾即可。

## 依賴套件

```
customtkinter, Pillow, pystray, keyboard, sounddevice, numpy, openai
```
