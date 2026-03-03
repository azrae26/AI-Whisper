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
- **句號快捷鍵**（預設 `Alt+\``）：第一次按開始錄音，第二次按停止並辨識；辨識完成後若游標在文字最後，會自動加句號再接辨識內容
- **逗號快捷鍵**（預設 `Insert`）：同上，但游標在文字最後時會加逗號再接辨識內容
- 皆可在設定頁自訂

> **注意：** `keyboard` 套件在 Windows 需要以系統管理員身份執行，全域快捷鍵才能在所有應用程式中作用。

## 設定項目

| 項目 | 說明 |
|------|------|
| API Key | OpenAI API Key，存在本機 config.json（exe 同目錄；開發時為 script 同目錄） |
| 辨識模型 | `gpt-4o-transcribe`（最強）/ `whisper-1`（舊版相容） |
| 全域快捷鍵 | 句號快捷鍵，格式如 `alt+\`` |
| 加逗號快捷鍵 | 辨識貼上時游標在文字最後加逗號，預設 `insert` |
| 開機啟動 | 寫入 Windows 登錄機碼自動啟動 |

## 打包成 .exe

```bash
python -m pip install pyinstaller
python -m PyInstaller --onedir --windowed --icon=assets/icon.ico --name="AI Whisper" --add-data "assets;assets" --version-file version_info.txt main.py
```

產出位於 `dist/AI Whisper/` 資料夾，將整個資料夾壓成 zip 分發即可。`config.json` 會產生在 exe 同目錄，設定可持久保存。

> 使用 `--onedir` 而非 `--onefile`，避免防毒軟體誤報（onefile 的自解壓行為類似惡意程式殼）。

## 依賴套件

```
customtkinter, Pillow, pystray, keyboard, sounddevice, numpy, openai
```
