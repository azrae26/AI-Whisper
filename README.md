# AI Whisper - 語音轉文字

按一下快捷鍵開始錄音，再按一下停止，辨識結果自動貼到游標處。

> **系統需求：** 僅支援 **Windows**，需以**系統管理員身份**執行，全域快捷鍵才能在所有應用程式中作用。

---

## 功能特色

- **一鍵錄音、自動貼上** — 按快捷鍵開始/停止，辨識完成後直接貼入游標所在位置
- **智慧標點符號** — 可選擇加句號或逗號快捷鍵，游標在文字最後時自動銜接
- **自動分段辨識** — 累積錄音達 18 秒且靜音超過 1 秒時，自動切段送出辨識，長句不中斷
- **歷史記錄** — 保留最近 10 筆辨識結果，可一鍵複製或用快捷鍵重貼
- **繁體中文輸出** — 自動將簡體轉為繁體（OpenCC），常見異體字一併校正
- **中英混合** — 中文語音夾雜英文單字可正確辨識
- **系統列常駐** — 關閉視窗後縮到系統列，錄音中圖示變紅
- **開機自動啟動** — 選擇性寫入 Windows Registry 開機執行

---

## 快速開始

### 安裝依賴

```bash
pip install -r requirements.txt
```

### 執行（開發模式）

```bash
python main.py
```

### 首次設定

1. 啟動後若無 API Key，會自動開啟設定頁面
2. 輸入 OpenAI API Key（格式：`sk-...`）
3. 選擇辨識模型（預設 `gpt-4o-transcribe`）
4. 設定完成後按「完成」即自動儲存

---

## 快捷鍵

| 快捷鍵 | 預設按鍵 | 說明 |
|--------|---------|------|
| 句號快捷鍵 | `Alt+\`` | 第一次按開始錄音，第二次按停止辨識；若游標在文字最後，貼上時自動加句號 `。` |
| 逗號快捷鍵 | `Insert` | 同上，貼上時自動加逗號 `，` |
| 記憶 1～5 | `Alt+Shift+1`～`Alt+Shift+5` | 重新貼上最近 5 筆辨識記錄 |

- 句號／逗號快捷鍵皆可在設定頁面自訂
- 記憶快捷鍵使用 Win32 `RegisterHotKey`，確保完全攔截不穿透

---

## 設定項目

| 項目 | 說明 | 預設值 |
|------|------|--------|
| API Key | OpenAI API Key，儲存於本機 `config.json` | — |
| 辨識模型 | 見下方模型說明 | `gpt-4o-transcribe` |
| 識別快捷鍵（句號） | 錄音啟動 / 停止並加句號 | `alt+\`` |
| 識別快捷鍵（逗號） | 錄音啟動 / 停止並加逗號 | `insert` |
| 歷史識別快捷鍵 1～5 | 重貼對應記憶 | `alt+shift+1`～`5` |
| 開機啟動 | 寫入 Windows Registry `HKCU\Run` | 開啟 |

設定儲存於 `config.json`（exe 同目錄；開發時為 script 同目錄），重新啟動後持久保存。

---

## 辨識模型

| 模型 | 說明 |
|------|------|
| `gpt-4o-transcribe` | 最高精準度，**預設推薦** |
| `gpt-4o-mini-transcribe` | 速度較快，精準度略低 |
| `whisper-1` | 舊版相容，費用較低 |

> API 若超過 2.5 秒無回應，會自動並行重試，取先回來的結果。

---

## 打包成 .exe

```bash
pip install pyinstaller
python -m PyInstaller --onedir --windowed --icon=assets/icon.ico --name="AI Whisper" --add-data "assets;assets" --version-file version_info.txt main.py
```

- 產出位於 `dist/AI Whisper/` 資料夾，將整個資料夾壓成 zip 分發即可
- `config.json` 會產生在 exe 同目錄，設定可持久保存
- 使用 `--onedir` 而非 `--onefile`，避免防毒軟體誤報（`--onefile` 的自解壓行為類似惡意程式殼）

---

## 依賴套件

```
customtkinter   # 現代化 UI 框架
Pillow          # 圖示處理
pystray         # 系統列常駐
keyboard        # 全域快捷鍵
sounddevice     # 麥克風錄音
numpy           # 音訊資料處理
openai          # OpenAI Whisper API
opencc-python-reimplemented  # 簡繁轉換
comtypes        # Windows COM 介面
uiautomation    # 游標位置偵測與自動貼上
```

---

## 注意事項

- `keyboard` 套件在 Windows 需要以**系統管理員身份**執行，全域快捷鍵才能在所有應用程式中作用
- API Key 僅存於本機 `config.json`，不會上傳
- 錄音資料僅傳送至 OpenAI 進行辨識，不會另外儲存
