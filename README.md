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
- 預設：`Ctrl+Shift+H`（可在設定中自訂）
- 第一次按：開始錄音
- 第二次按：停止並辨識
- 辨識完成後文字自動貼到當前游標位置

> **注意：** `keyboard` 套件在 Windows 需要以系統管理員身份執行，全域快捷鍵才能在所有應用程式中作用。

## 設定項目

| 項目 | 說明 |
|------|------|
| API Key | OpenAI API Key，存在本機 config.json（exe 同目錄；開發時為 script 同目錄） |
| 辨識模型 | `gpt-4o-transcribe`（最強）/ `whisper-1`（舊版相容） |
| 全域快捷鍵 | 格式如 `ctrl+shift+h` |
| 開機啟動 | 寫入 Windows 登錄機碼自動啟動 |

## 打包成 .exe

```bash
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --icon=assets/icon.ico --name="AI Whisper" main.py
```

產出位於 `dist/AI Whisper.exe`，分享時附上 `assets/` 資料夾即可。首次執行後，`config.json` 會產生在 exe 同目錄，設定可持久保存。

## 依賴套件

```
customtkinter, Pillow, pystray, keyboard, sounddevice, numpy, openai
```
