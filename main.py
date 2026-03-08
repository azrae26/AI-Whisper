# 功能：AI Whisper 語音轉文字主程式
# 職責：UI 管理（主頁面/設定頁面）、系統列常駐、全域快捷鍵、錄音與辨識流程協調、辨識結果文字校正
# 依賴：customtkinter, pystray, keyboard, recorder, transcriber, paster, settings

import ctypes
import math
import os
import sys
import queue
import threading
import time
import datetime

# 修正 console 編碼，讓中文不會變問號
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
except Exception:
    pass

# 在任何 tkinter 初始化之前，鎖定為 System DPI Awareness
# 跨螢幕移動時由 Windows GPU 做點陣圖縮放，避免 tkinter 逐 widget 重算造成 lag
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
except Exception:
    pass

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk
import pystray
import keyboard

import settings
import recorder as rec_module
import transcriber
import paster
import waveform

# ── 路徑 ─────────────────────────────────────────────────────────────────────
# 打包後 assets 在 _MEIPASS 暫存目錄內；開發時在 script 同目錄
_meipass = getattr(sys, '_MEIPASS', None)
if _meipass:
    ASSETS_DIR = os.path.join(_meipass, 'assets')
else:
    ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
ICON_PATH = os.path.join(ASSETS_DIR, 'icon_256.png')

# ── 自動分段參數 ──────────────────────────────────────────────────────────────
AUTO_SEGMENT_MAX_ACCUM_SEC = 18.0   # 累積超過此長度後，短靜音即觸發送出
AUTO_SEGMENT_SHORT_SILENCE_SEC = 1.0  # 搭配累積夠長時的靜音門檻
AUTO_SEGMENT_SILENCE_SEC = 2.0      # 靜音超過此秒數直接送出

# ── 全域狀態 ──────────────────────────────────────────────────────────────────
recorder = rec_module.Recorder()
_hotkey_handle = None  # keyboard hook handle


def now_str() -> str:
    return datetime.datetime.now().strftime('%H:%M:%S')


def _debug_print(msg: str):
    """輸出 debug 訊息，Windows cp950 無法顯示 emoji 時自動降級"""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))

# 文字校正：支援多種分隔符
_TEXT_CORRECTION_DELIMITERS = ('→', '=', ',', ':', '|', '\t')


def _parse_text_corrections(text: str) -> list[dict]:
    """將大框文字解析為 [{"from":..., "to":...}]，支援多種分隔符"""
    result = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in _TEXT_CORRECTION_DELIMITERS:
            if sep in line:
                parts = line.split(sep, 1)
                if len(parts) == 2 and parts[0].strip():
                    result.append({'from': parts[0].strip(), 'to': parts[1].strip()})
                break
    return result


def _apply_text_corrections(text: str) -> str:
    """依 config 的 text_corrections 對辨識結果做替換"""
    corrections = settings.get().get('text_corrections', [])
    for item in corrections:
        src = item.get('from', '')
        if src:
            text = text.replace(src, item.get('to', ''))
    return text


# ═════════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('blue')

        self.title('AI Whisper')
        self.minsize(380, 520)
        self.resizable(True, True)
        self.configure(fg_color='#121212')

        # 恢復上次視窗位置與大小
        saved_geo = settings.get().get('geometry')
        self.geometry(saved_geo if saved_geo else '420x580')

        # 視窗圖示延遲設定（CustomTkinter 在 Windows 會覆蓋，需在 init 之後設定）
        self.after(250, self._set_window_icon)

        # 關閉按鈕 -> 縮到系統列
        self.protocol('WM_DELETE_WINDOW', self._on_close)

        # 初始化 paster 剪貼簿橋接
        paster.set_tk_root(self)

        # 讀取設定
        self._cfg = settings.get()

        # 狀態
        self._state = 'idle'  # idle | recording | processing
        self._page = 'main'   # main | settings
        self._paste_prefix = '。'  # 游標在文字最後時加在辨識內容前的符號（句號/逗號，由快捷鍵決定）
        self._anim_dots = 0
        self._anim_job = None
        self._history: list[str] = []  # 最近 10 組辨識結果
        self._segment_check_job = None  # 分段辨識定時檢查 job

        self._build_ui()
        self._waveform_overlay = waveform.WaveformOverlay(self)
        self._register_hotkey()
        self._start_tray()

        # 視窗移動/縮放時自動儲存位置（debounce 1s，避免頻繁寫檔）
        self._geo_save_job = None
        self.bind('<Configure>', self._on_configure)

        # 若無 API Key，啟動後自動開設定頁
        if not self._cfg.get('apiKey'):
            self.after(300, self._show_settings)

    def _set_window_icon(self):
        """用 Win32 API 直接設定視窗圖示，繞過 CustomTkinter 覆蓋問題"""
        path = os.path.abspath(ICON_PATH)
        if not os.path.exists(path):
            return
        try:
            ico_path = os.path.join(ASSETS_DIR, 'icon.ico')
            if not os.path.exists(ico_path):
                src = Image.open(path)
                src.save(ico_path, format='ICO', sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
            if sys.platform == 'win32' and os.path.exists(ico_path):
                user32 = ctypes.windll.user32
                hwnd = int(self.wm_frame(), 16)
                # 依 DPI 決定最佳尺寸
                cx_big = user32.GetSystemMetrics(11)    # SM_CXICON
                cx_small = user32.GetSystemMetrics(49)  # SM_CXSMICON
                ico_w = ctypes.c_wchar_p(ico_path)
                LR_LOADFROMFILE = 0x10
                LR_DEFAULTSIZE = 0x40
                IMAGE_ICON = 1
                icon_big = user32.LoadImageW(None, ico_w, IMAGE_ICON, cx_big, cx_big, LR_LOADFROMFILE)
                icon_small = user32.LoadImageW(None, ico_w, IMAGE_ICON, cx_small, cx_small, LR_LOADFROMFILE)
                WM_SETICON = 0x0080
                if icon_big:
                    user32.SendMessageW(hwnd, WM_SETICON, 1, icon_big)    # ICON_BIG
                if icon_small:
                    user32.SendMessageW(hwnd, WM_SETICON, 0, icon_small)  # ICON_SMALL
        except Exception:
            pass

    # ── 圖示繪製 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _make_back_arrow(size: int = 20, color: str = '#A1A1AA', stroke: float = 2.2, scale: int = 2) -> Image.Image:
        """用 PIL 畫左箭頭 chevron，回傳 RGBA Image"""
        s = size * scale
        sw = max(2, round(stroke * scale))
        img = Image.new('RGBA', (s, s), (0, 0, 0, 0))  # type: ignore[arg-type]
        d = ImageDraw.Draw(img)
        c = tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) + (255,)  # type: ignore[arg-type]

        # tip（左尖端），arm_top / arm_bot（右上/右下端點）
        tip  = (round(s * 0.28), round(s * 0.50))
        atop = (round(s * 0.68), round(s * 0.15))
        abot = (round(s * 0.68), round(s * 0.85))

        d.line([atop, tip], fill=c, width=sw)
        d.line([tip,  abot], fill=c, width=sw)

        # 圓頭端點
        r = sw // 2
        for x, y in [atop, tip, abot]:
            d.ellipse([x - r, y - r, x + r, y + r], fill=c)

        return img

    # ── UI 建構 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # 主容器（用來切換主頁面 / 設定頁面）
        self._main_frame = self._build_main_frame()
        self._settings_frame = self._build_settings_frame()

        self._main_frame.grid(row=0, column=0, sticky='nsew')
        self._settings_frame.grid(row=0, column=0, sticky='nsew')
        self._show_main()

    def _build_main_frame(self) -> ctk.CTkFrame:
        font_family = "Microsoft JhengHei UI"
        frame = ctk.CTkFrame(self, fg_color='transparent')
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # ── 頂部標題列
        top = ctk.CTkFrame(frame, fg_color='#18181B', corner_radius=0, height=56)
        top.grid(row=0, column=0, sticky='ew')
        top.grid_propagate(False)

        # logo + AI Whisper 絕對置中（place relx=0.5 不受右側按鈕影響）
        title_frame = ctk.CTkFrame(top, fg_color='transparent')
        title_frame.place(relx=0.5, rely=0.5, anchor='center')
        if os.path.exists(ICON_PATH):
            logo_img = ctk.CTkImage(Image.open(ICON_PATH), size=(23, 23))
            ctk.CTkLabel(title_frame, image=logo_img, text='').pack(side='left')
            self._header_logo = logo_img  # 防 GC
        ctk.CTkLabel(
            title_frame, text='AI Whisper', font=ctk.CTkFont(family=font_family, size=18, weight='bold'),
            text_color='#F4F4F5'
        ).pack(side='left', padx=(8, 0))

        ctk.CTkButton(
            top, text='•••', width=40, height=40,
            fg_color='transparent', hover_color='#27272A',
            font=ctk.CTkFont(family=font_family, size=16, weight='bold'), text_color='#A1A1AA',
            command=self._show_settings
        ).place(relx=1.0, rely=0.5, anchor='e', x=-12)

        # ── content_area：佔滿標題列以下所有空間，子 widget 用 place() 定位
        content_area = ctk.CTkFrame(frame, fg_color='transparent')
        content_area.grid(row=1, column=0, sticky='nsew')
        self._content_area = content_area

        # mic_container：初始位置約垂直置中偏上（rely=0.35 anchor='n'），有歷史後動畫移至頂部
        mic_container = ctk.CTkFrame(content_area, fg_color='transparent')
        mic_container.place(relx=0.5, rely=0.35, anchor='n')
        self._mic_container = mic_container
        self._mic_centered = True

        self._mic_btn = ctk.CTkButton(
            mic_container,
            text='開始錄音',
            width=200, height=56,
            corner_radius=28,
            font=ctk.CTkFont(family=font_family, size=18, weight='bold'),
            fg_color='#27272A',
            hover_color='#3F3F46',
            border_width=2,
            border_color='#3F3F46',
            text_color='#F4F4F5',
            command=self._toggle_recording,
        )
        self._mic_btn.pack()

        # 快捷鍵提示
        self._hotkey_label = ctk.CTkLabel(
            mic_container,
            text=self._hotkey_display(),
            font=ctk.CTkFont(family=font_family, size=12),
            text_color='#71717A',
        )
        self._hotkey_label.pack(pady=(12, 0))

        # 狀態標籤（等待中時隱藏）
        # 錄音狀態列：「● 錄音中：」和「MM:SS」拆為兩個 Label，計時器單獨往下 1px
        # padx=(0,2) 使整體往左偏 1px
        self._status_frame = ctk.CTkFrame(mic_container, fg_color='transparent')
        self._status_frame.pack(pady=(4, 0), padx=(0, 2))

        status_font = ctk.CTkFont(family=font_family, size=14, weight='bold')
        self._status_label = ctk.CTkLabel(
            self._status_frame, text='',
            font=status_font, text_color='#A1A1AA',
        )
        self._status_label.pack(side='left')

        self._timer_label = ctk.CTkLabel(
            self._status_frame, text='',
            font=status_font, text_color='#A1A1AA',
        )
        self._timer_label.pack(side='left', pady=(2, 0))

        # ── 結果區（初始隱藏，有歷史記錄後由動畫顯示）
        self._result_scroll = ctk.CTkScrollableFrame(
            content_area, fg_color='transparent', corner_radius=0,
            scrollbar_button_color='#121212', scrollbar_button_hover_color='#3F3F46',
        )
        self._result_scroll.grid_columnconfigure(0, weight=1)
        self._history_widgets: list[ctk.CTkFrame] = []

        return frame

    def _build_settings_frame(self) -> ctk.CTkFrame:
        font_family = "Microsoft JhengHei UI"
        frame = ctk.CTkFrame(self, fg_color='transparent')
        frame.grid_columnconfigure(0, weight=1)

        # ── 頂部
        top = ctk.CTkFrame(frame, fg_color='#18181B', corner_radius=0, height=56)
        top.grid(row=0, column=0, sticky='ew')
        top.grid_columnconfigure(1, weight=1)
        top.grid_propagate(False)

        _arrow_img = self._make_back_arrow(size=20, color='#A1A1AA', stroke=2.2, scale=2)
        _arrow_ctk = ctk.CTkImage(light_image=_arrow_img, dark_image=_arrow_img, size=(20, 20))
        ctk.CTkButton(
            top, text='', image=_arrow_ctk, width=36, height=36,
            fg_color='transparent', hover_color='#27272A',
            command=self._show_main,
        ).grid(row=0, column=0, padx=(12, 2), pady=10)

        ctk.CTkLabel(
            top, text='設定', font=ctk.CTkFont(family=font_family, size=18, weight='bold'),
            text_color='#F4F4F5'
        ).grid(row=0, column=1, pady=14, sticky='w', padx=(2, 0))

        ctk.CTkButton(
            top, text='完成', width=68, height=34,
            corner_radius=8,
            fg_color='#6C63FF', hover_color='#5A52E0',
            border_width=0,
            font=ctk.CTkFont(family=font_family, size=15, weight='bold'),
            text_color='#FFFFFF',
            command=self._show_main,
        ).grid(row=0, column=2, padx=(4, 20), pady=10)

        # ── 設定內容捲動區
        scroll = ctk.CTkScrollableFrame(
            frame, fg_color='transparent',
            scrollbar_button_color='#121212', scrollbar_button_hover_color='#3F3F46',
        )
        # padx 右側縮小以補償 CTkScrollableFrame 右側捲軸寬度，使左右視覺間距對稱
        scroll.grid(row=1, column=0, sticky='nsew', padx=(20, 12), pady=16)
        frame.grid_rowconfigure(1, weight=1)
        scroll.grid_columnconfigure(0, weight=1)

        row = 0

        def add_label(text):
            lbl = ctk.CTkLabel(scroll, text=text, font=ctk.CTkFont(family=font_family, size=14, weight='bold'),
                               text_color='#D4D4D8')
            lbl.grid(row=row, column=0, sticky='w', pady=(0, 6))

        # ── 1. 開機啟動
        add_label('開機時自動啟動')
        row += 1

        startup_frame = ctk.CTkFrame(scroll, fg_color='transparent')
        startup_frame.grid(row=row, column=0, sticky='ew', pady=(0, 20))
        row += 1

        self._startup_var = ctk.BooleanVar(value=settings.is_startup_enabled())
        self._startup_var.trace_add('write', lambda *_: self._auto_save())
        ctk.CTkSwitch(
            startup_frame,
            text='',
            variable=self._startup_var,
            onvalue=True, offvalue=False,
            progress_color='#2563EB'
        ).pack(side='left')

        # ── 2. 識別快捷鍵（自動加句號）
        add_label('識別快捷鍵（自動加句號）')
        row += 1

        ctk.CTkLabel(
            scroll, text='辨識貼上時，游標在文字最後會加句號；點擊按鈕可自訂（Esc 取消）',
            font=ctk.CTkFont(family=font_family, size=12), text_color='#71717A',
        ).grid(row=row, column=0, sticky='w', pady=(0, 6))
        row += 1

        self._hotkey_var = ctk.StringVar(value=self._cfg.get('hotkey', 'alt+`'))
        self._hotkey_capture_btn = ctk.CTkButton(
            scroll,
            text=self._cfg.get('hotkey', 'alt+`').upper(),
            height=40,
            corner_radius=8,
            fg_color='#27272A', hover_color='#3F3F46',
            border_width=1, border_color='#3F3F46',
            font=ctk.CTkFont(family=font_family, size=15, weight='bold'),
            text_color='#F4F4F5',
            command=lambda: self._start_hotkey_capture(self._hotkey_var, self._hotkey_capture_btn),
        )
        self._hotkey_capture_btn.grid(row=row, column=0, sticky='ew', pady=(0, 6))
        row += 1

        # ── 2b. 識別快捷鍵（自動加逗號）
        add_label('識別快捷鍵（自動加逗號）')
        row += 1

        ctk.CTkLabel(
            scroll, text='辨識貼上時，游標在文字最後會加逗號；點擊按鈕可自訂（Esc 取消）',
            font=ctk.CTkFont(family=font_family, size=12), text_color='#71717A',
        ).grid(row=row, column=0, sticky='w', pady=(0, 6))
        row += 1

        self._hotkey_comma_var = ctk.StringVar(value=self._cfg.get('hotkey_comma', 'insert'))
        self._hotkey_comma_capture_btn = ctk.CTkButton(
            scroll,
            text=self._cfg.get('hotkey_comma', 'insert').upper(),
            height=40,
            corner_radius=8,
            fg_color='#27272A', hover_color='#3F3F46',
            border_width=1, border_color='#3F3F46',
            font=ctk.CTkFont(family=font_family, size=15, weight='bold'),
            text_color='#F4F4F5',
            command=lambda: self._start_hotkey_capture(self._hotkey_comma_var, self._hotkey_comma_capture_btn),
        )
        self._hotkey_comma_capture_btn.grid(row=row, column=0, sticky='ew', pady=(0, 20))
        row += 1

        # ── 3. 歷史識別快捷鍵
        add_label('歷史識別快捷鍵')
        row += 1

        ctk.CTkLabel(
            scroll, text='點擊按鈕後，按下想要的組合鍵（Esc 取消）',
            font=ctk.CTkFont(family=font_family, size=12), text_color='#71717A',
        ).grid(row=row, column=0, sticky='w', pady=(0, 6))
        row += 1

        history_hotkeys_cfg = self._cfg.get(
            'history_hotkeys',
            ['alt+shift+1', 'alt+shift+2', 'alt+shift+3', 'alt+shift+4', 'alt+shift+5'],
        )
        self._history_hotkey_vars: list[ctk.StringVar] = []
        self._history_hotkey_btns: list[ctk.CTkButton] = []
        for i in range(5):
            hk_row_frame = ctk.CTkFrame(scroll, fg_color='transparent')
            hk_row_frame.grid(row=row, column=0, sticky='ew', pady=(0, 6))
            hk_row_frame.grid_columnconfigure(1, weight=1)
            row += 1

            ctk.CTkLabel(
                hk_row_frame, text=f'記憶 {i + 1}',
                font=ctk.CTkFont(family=font_family, size=13),
                text_color='#A1A1AA', width=56, anchor='w',
            ).grid(row=0, column=0, padx=(0, 8))

            hk = history_hotkeys_cfg[i] if i < len(history_hotkeys_cfg) else f'alt+shift+{i + 1}'
            var = ctk.StringVar(value=hk)
            self._history_hotkey_vars.append(var)

            btn = ctk.CTkButton(
                hk_row_frame,
                text=hk.upper(),
                height=36,
                corner_radius=8,
                fg_color='#27272A', hover_color='#3F3F46',
                border_width=1, border_color='#3F3F46',
                font=ctk.CTkFont(family=font_family, size=13, weight='bold'),
                text_color='#F4F4F5',
                command=lambda v=var: None,  # placeholder，configure 後才正確綁定
            )
            btn.configure(command=lambda v=var, b=btn: self._start_hotkey_capture(v, b))
            btn.grid(row=0, column=1, sticky='ew')
            self._history_hotkey_btns.append(btn)

        # 歷史快捷鍵區塊結束後補足與下一區塊的間距
        ctk.CTkFrame(scroll, fg_color='transparent', height=14).grid(row=row, column=0, sticky='ew')
        row += 1

        # ── 4. API Key
        add_label('OpenAI API Key')
        row += 1

        key_frame = ctk.CTkFrame(scroll, fg_color='transparent')
        key_frame.grid(row=row, column=0, sticky='ew', pady=(0, 20))
        key_frame.grid_columnconfigure(0, weight=1)
        row += 1

        self._api_key_var = ctk.StringVar(value=self._cfg.get('apiKey', ''))
        self._api_key_entry = ctk.CTkEntry(
            key_frame, textvariable=self._api_key_var,
            show='•', placeholder_text='sk-...',
            height=40, font=ctk.CTkFont(family=font_family, size=14),
            fg_color='#27272A', border_color='#3F3F46'
        )
        self._api_key_entry.grid(row=0, column=0, sticky='ew')
        self._api_key_entry.bind('<FocusOut>', lambda e: self._auto_save())

        self._show_key_btn = ctk.CTkButton(
            key_frame, text='👁', width=44, height=40,
            fg_color='#3F3F46', hover_color='#52525B',
            font=ctk.CTkFont(family=font_family, size=16),
            command=self._toggle_key_visibility,
        )
        self._show_key_btn.grid(row=0, column=1, padx=(8, 0))
        self._key_visible = False

        # ── 5. 辨識模型
        add_label('辨識模型')
        row += 1

        self._model_var = ctk.StringVar(value=self._cfg.get('model', 'gpt-4o-transcribe'))
        self._model_var.trace_add('write', lambda *_: self._auto_save())
        ctk.CTkOptionMenu(
            scroll,
            values=transcriber.SUPPORTED_MODELS,
            variable=self._model_var,
            height=40,
            font=ctk.CTkFont(family=font_family, size=14),
            dropdown_font=ctk.CTkFont(family=font_family, size=13),
            fg_color='#3F3F46',
            button_color='#52525B',
            button_hover_color='#71717A',
        ).grid(row=row, column=0, sticky='ew', pady=(0, 20))
        row += 1

        # ── 6. 文字校正
        add_label('文字校正')
        row += 1

        ctk.CTkLabel(
            scroll,
            text='每行一組，格式：原字,替換字；辨識結果會自動替換',
            font=ctk.CTkFont(family=font_family, size=12), text_color='#71717A',
        ).grid(row=row, column=0, sticky='w', pady=(0, 6))
        row += 1

        corrections_cfg = self._cfg.get('text_corrections', [])
        corrections_lines = [f"{c.get('from', '')}→{c.get('to', '')}" for c in corrections_cfg if c.get('from')]
        _LINE_HEIGHT = 24
        _MIN_HEIGHT, _MAX_HEIGHT = 90, 300
        self._text_correction_textbox = ctk.CTkTextbox(
            scroll, height=90,
            font=ctk.CTkFont(family=font_family, size=14),
            fg_color='#27272A', border_color='#3F3F46',
        )
        self._text_correction_textbox.grid(row=row, column=0, sticky='ew', pady=(0, 20))
        self._text_correction_textbox.insert('1.0', '\n'.join(corrections_lines))
        self._text_correction_textbox.bind('<FocusOut>', lambda e: self._auto_save())
        self._text_correction_textbox.bind('<KeyRelease>', self._on_text_correction_change)
        self._text_correction_textbox.bind('<<Paste>>', lambda e: self.after(50, lambda: self._on_text_correction_change()))

        # 滑鼠在文字框上時，滾輪事件只作用於文字框，不向上冒泡到設定頁捲動區
        def _block_scroll(e):
            self._text_correction_textbox._textbox.yview_scroll(int(-1 * (e.delta / 120)), 'units')
            return 'break'
        self._text_correction_textbox.bind('<MouseWheel>', _block_scroll)

        def _resize_textbox():
            content = self._text_correction_textbox.get('1.0', 'end')
            line_count = max(4, len(content.strip().splitlines())) if content.strip() else 4
            new_h = min(_MAX_HEIGHT, max(_MIN_HEIGHT, line_count * _LINE_HEIGHT))
            self._text_correction_textbox.configure(height=new_h)

        self._resize_text_correction_textbox = _resize_textbox
        _resize_textbox()
        row += 1

        return frame

    # ── 頁面切換 ──────────────────────────────────────────────────────────────

    def _show_main(self):
        self._page = 'main'
        self._settings_frame.grid_remove()
        self._main_frame.grid()
        self._main_frame.tkraise()

    def _show_settings(self):
        self._page = 'settings'
        self._main_frame.grid_remove()
        self._settings_frame.grid()
        self._settings_frame.tkraise()

    # ── 設定操作 ──────────────────────────────────────────────────────────────

    def _toggle_key_visibility(self):
        self._key_visible = not self._key_visible
        self._api_key_entry.configure(show='' if self._key_visible else '•')

    def _on_text_correction_change(self, event=None):
        """文字校正框內容變動時自動擴展高度"""
        if hasattr(self, '_resize_text_correction_textbox'):
            self._resize_text_correction_textbox()

    _MODIFIERS = {'ctrl', 'shift', 'alt', 'left ctrl', 'right ctrl',
                   'left shift', 'right shift', 'left alt', 'right alt',
                   'left windows', 'right windows', 'windows'}
    _MOD_NORMALIZE = {
        'left ctrl': 'ctrl', 'right ctrl': 'ctrl',
        'left shift': 'shift', 'right shift': 'shift',
        'left alt': 'alt', 'right alt': 'alt',
        'left windows': 'windows', 'right windows': 'windows',
    }

    def _start_hotkey_capture(self, var: ctk.StringVar, btn: ctk.CTkButton):
        """進入快捷鍵捕捉模式，用 keyboard.hook 追蹤按鍵組合（var/btn 為目標輸入框與按鈕）"""
        self._capture_var = var
        self._capture_btn = btn
        btn.configure(
            text='請按下組合鍵…',
            fg_color='#1E3A5F', border_color='#2563EB', text_color='#93C5FD',
        )
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self._capture_keys = set()
        self._capturing = True
        keyboard.hook(self._on_capture_event)

    def _on_capture_event(self, event):
        if not self._capturing:
            return
        name = event.name.lower()
        if event.event_type == keyboard.KEY_DOWN:
            if name == 'esc':
                self._capturing = False
                self.after(0, self._finish_capture_cancel)
                return
            normalized = self._MOD_NORMALIZE.get(name, name)
            self._capture_keys.add(normalized)
        elif event.event_type == keyboard.KEY_UP:
            if self._capture_keys:
                keys = self._capture_keys.copy()
                self._capturing = False
                mod_order = ['ctrl', 'shift', 'alt', 'windows']
                mods = [k for k in mod_order if k in keys]
                others = sorted(k for k in keys if k not in mod_order)
                if others:
                    hotkey = '+'.join(mods + others)
                    self.after(0, lambda h=hotkey: self._finish_capture_ok(h))

    def _finish_capture_ok(self, hotkey: str):
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self._capture_var.set(hotkey)
        self._capture_btn.configure(
            text=hotkey.upper(),
            fg_color='#27272A', border_color='#3F3F46', text_color='#F4F4F5',
        )
        self._auto_save()

    def _finish_capture_cancel(self):
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        hk = self._capture_var.get()
        self._capture_btn.configure(
            text=hk.upper(),
            fg_color='#27272A', border_color='#3F3F46', text_color='#F4F4F5',
        )
        self._auto_save()

    def _auto_save(self):
        """設定變動時靜默自動儲存，不跳頁、不顯示訊息"""
        text_corrections_raw = self._text_correction_textbox.get('1.0', 'end')
        new_cfg = {
            'apiKey': self._api_key_var.get().strip(),
            'model': self._model_var.get(),
            'hotkey': self._hotkey_var.get().strip().lower(),
            'hotkey_comma': self._hotkey_comma_var.get().strip().lower(),
            'history_hotkeys': [v.get().strip().lower() for v in self._history_hotkey_vars],
            'text_corrections': _parse_text_corrections(text_corrections_raw),
            'startup': self._startup_var.get(),
        }
        settings.save(new_cfg)
        settings.set_startup(new_cfg['startup'])
        self._cfg = settings.get()
        self._register_hotkey()
        self._hotkey_label.configure(text=self._hotkey_display())

    # ── 錄音控制 ──────────────────────────────────────────────────────────────

    def _toggle_recording(self, paste_prefix: str = '。'):
        """paste_prefix：游標在文字最後時加在辨識內容前的符號（句號或逗號），由快捷鍵決定"""
        if self._state == 'idle':
            self._paste_prefix = paste_prefix
            self._start_recording()
        elif self._state == 'recording':
            self._stop_recording()

    def _start_recording(self):
        ok = recorder.start()
        if not ok:
            self._set_status('❌ 無法存取麥克風', '#EF4444')
            return

        self._state = 'recording'
        self._mic_btn.configure(
            text='停止錄音',
            fg_color='#520000', hover_color='#7A0000',
            border_color='#EF4444', text_color='#FECACA'
        )
        self._set_status('錄音中', '#EF4444')
        self._set_tray_icon('recording')
        self._waveform_overlay.show()
        self._start_anim()
        _debug_print(f'[main][{now_str()}] 🎙️ 開始錄音')
        # 啟動分段辨識定時檢查（每 200ms 一次）
        self._segment_check_job = self.after(200, self._check_segment)

    def _stop_recording(self):
        # 取消分段定時檢查
        if self._segment_check_job:
            self.after_cancel(self._segment_check_job)
            self._segment_check_job = None

        self._state = 'processing'
        self._stop_anim()
        self._waveform_overlay.show_processing()
        self._mic_btn.configure(
            text='處理中…',
            fg_color='#1E1E24', hover_color='#1E1E24',
            border_color='#4B5563', text_color='#A1A1AA',
            state='disabled'
        )
        self._set_status('辨識中…', '#A78BFA')

        wav_bytes = recorder.stop()
        if not wav_bytes:
            self._reset_idle()
            self._set_status('⚠ 未錄到音訊', '#F59E0B')
            self.after(2000, lambda: self._set_status('等待中', '#A1A1AA'))
            return

        _debug_print(f'[main][{now_str()}] ✅ 錄音完成，送出辨識')
        paster.prefetch_cursor_position(len(wav_bytes))
        threading.Thread(target=self._run_transcribe, args=(wav_bytes,), daemon=True).start()

    def _check_segment(self):
        """每 200ms 檢查是否達到自動分段條件（累積 >= AUTO_SEGMENT_MAX_ACCUM_SEC 且靜音 >= AUTO_SEGMENT_SHORT_SILENCE_SEC，或靜音 >= AUTO_SEGMENT_SILENCE_SEC 立即送出）"""
        if self._state != 'recording':
            return
        accumulated = recorder.get_accumulated_seconds()
        silence = recorder.get_silence_seconds()
        if (accumulated >= AUTO_SEGMENT_MAX_ACCUM_SEC and silence >= AUTO_SEGMENT_SHORT_SILENCE_SEC) or silence >= AUTO_SEGMENT_SILENCE_SEC:
            wav_bytes = recorder.flush_segment()
            if wav_bytes:
                reason = '累積夠長+短靜音' if (accumulated >= AUTO_SEGMENT_MAX_ACCUM_SEC and silence >= AUTO_SEGMENT_SHORT_SILENCE_SEC) else f'靜音達{AUTO_SEGMENT_SILENCE_SEC:.0f}s'
                _debug_print(f'[main][{now_str()}] ✂️ 自動分段送出（{reason}，累積 {accumulated:.1f}s，靜音 {silence:.1f}s）')
                paster.prefetch_cursor_position(len(wav_bytes))
                threading.Thread(
                    target=self._run_segment_transcribe, args=(wav_bytes,), daemon=True
                ).start()
        self._segment_check_job = self.after(200, self._check_segment)

    @staticmethod
    def _transcribe_with_retry(wav_bytes: bytes, api_key: str, model: str,
                               timeout: float = 2.5) -> str:
        """呼叫 API，超過 timeout 秒未回應則並行重試，取先回來的結果"""
        result_q: queue.Queue = queue.Queue()

        def _call(attempt: int):
            try:
                text = transcriber.transcribe(wav_bytes, api_key=api_key, model=model)
                result_q.put(('ok', text, attempt))
            except Exception as e:
                result_q.put(('error', str(e), attempt))

        threading.Thread(target=_call, args=(1,), daemon=True).start()

        try:
            status, payload, attempt = result_q.get(timeout=timeout)
        except queue.Empty:
            _debug_print(f'[main][{now_str()}] ⚠️ API 超過 {timeout}s 未回應，重試中…')
            threading.Thread(target=_call, args=(2,), daemon=True).start()
            status, payload, attempt = result_q.get()

        if attempt == 2:
            _debug_print(f'[main][{now_str()}] 🔄 使用重試結果')

        if status == 'ok':
            return payload
        raise Exception(payload)

    def _run_segment_transcribe(self, wav_bytes: bytes):
        """分段辨識 thread：辨識完成後貼上並加入歷史，不影響錄音狀態"""
        cfg = settings.get()
        api_key = cfg.get('apiKey', '')
        model = cfg.get('model', 'gpt-4o-transcribe')
        if not api_key:
            return
        try:
            text = self._transcribe_with_retry(wav_bytes, api_key, model)
            t_received = time.perf_counter()
            text_clean = text.rstrip('。')
            text_clean = _apply_text_corrections(text_clean)
            _debug_print(f'[main][{now_str()}] ✅ 分段辨識完成: "{text_clean}"')
            if text_clean:
                paster.paste_text(text_clean, delay_ms=30, t_received=t_received, end_prefix=self._paste_prefix)
            self.after(0, lambda: self._on_segment_done(text_clean))
        except Exception as e:
            _debug_print(f'[main][{now_str()}] ❌ 分段辨識失敗: {e}')

    def _on_segment_done(self, text: str):
        """分段辨識完成 UI 更新：顯示結果並加入歷史，保持錄音中狀態不重置"""
        if not text:
            return
        self._set_result(text)

    def _run_transcribe(self, wav_bytes: bytes):
        cfg = settings.get()
        api_key = cfg.get('apiKey', '')
        model = cfg.get('model', 'gpt-4o-transcribe')

        if not api_key:
            self.after(0, lambda: self._set_status('❌ 請先設定 API Key', '#EF4444'))
            self.after(0, self._reset_idle)
            self.after(0, self._show_settings)
            return

        try:
            text = self._transcribe_with_retry(wav_bytes, api_key, model)
            t_received = time.perf_counter()
            text_clean = text.rstrip('。')
            text_clean = _apply_text_corrections(text_clean)
            _debug_print(f'[main][{now_str()}] ✅ 辨識完成: "{text_clean}"')
            if text_clean:
                paster.paste_text(text_clean, t_received=t_received, end_prefix=self._paste_prefix)
            self.after(0, lambda: self._on_transcribe_done(text_clean))
        except Exception as e:
            err_msg = str(e)
            _debug_print(f'[main][{now_str()}] ❌ 辨識失敗: {err_msg}')
            self.after(0, lambda: self._on_transcribe_error(err_msg))

    def _on_transcribe_done(self, text: str):
        # UI 更新：重置狀態、顯示辨識結果
        self._reset_idle()
        self._set_result(text)
        self._set_status('辨識完成 ✓', '#10B981')
        self.after(2000, lambda: self._set_status('等待中', '#A1A1AA'))

    def _on_transcribe_error(self, err_msg: str):
        self._reset_idle()
        short = err_msg[:60] + '…' if len(err_msg) > 60 else err_msg
        self._set_status(f'❌ {short}', '#EF4444')
        self.after(4000, lambda: self._set_status('等待中', '#A1A1AA'))

    def _reset_idle(self):
        self._state = 'idle'
        self._waveform_overlay.hide()
        self._mic_btn.configure(
            text='開始錄音',
            fg_color='#27272A', hover_color='#3F3F46',
            border_color='#3F3F46', text_color='#F4F4F5',
            state='normal',
        )
        self._set_tray_icon('idle')

    # ── 動畫 ─────────────────────────────────────────────────────────────────

    _PULSE_DIM = (200, 60, 60)       # 暗紅
    _PULSE_BRIGHT = (255, 180, 180)  # 亮紅

    @staticmethod
    def _lerp_color(c1: tuple, c2: tuple, t: float) -> str:
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        return f'#{r:02x}{g:02x}{b:02x}'

    def _start_anim(self):
        self._rec_start_time = time.time()
        self._tick_anim()

    def _tick_anim(self):
        if self._state != 'recording':
            return

        elapsed = time.time() - self._rec_start_time
        minutes = int(elapsed) // 60
        seconds = int(elapsed) % 60

        # sin 波產生 0~1 平滑值，週期 2 秒
        t = (math.sin(elapsed * math.pi) + 1) / 2

        color = self._lerp_color(self._PULSE_DIM, self._PULSE_BRIGHT, t)
        self._status_label.configure(text='● 錄音中：', text_color=color)
        self._timer_label.configure(text=f'{minutes:02d}:{seconds:02d}', text_color=color)
        self._mic_btn.configure(border_color=color)

        # 更新波形覆蓋層
        wf_data = recorder.get_waveform()
        self._waveform_overlay.update(wf_data)

        self._anim_job = self.after(33, self._tick_anim)  # ~30fps

    def _stop_anim(self):
        if self._anim_job:
            self.after_cancel(self._anim_job)
            self._anim_job = None

    # ── UI 更新工具 ───────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = '#A1A1AA'):
        display = '' if text == '等待中' else text
        self._status_label.configure(text=display, text_color=color)
        self._timer_label.configure(text='', text_color=color)

    def _animate_mic_up(self, step: int = 0):
        """mic_container 從垂直置中向上滑動至頂部（ease-in-out），動畫結束後顯示結果捲動區"""
        START = 0.35
        TARGET = 0.07
        TOTAL = 16       # 總幀數：16 × 11ms ≈ 176ms
        INTERVAL = 11    # ms per frame
        if step >= TOTAL:
            self._mic_container.place(relx=0.5, rely=TARGET, anchor='n')
            self._mic_centered = False
            self._result_scroll.place(relx=0.05, rely=0.33, relwidth=0.93, relheight=0.67)
            return
        t = step / TOTAL
        # smoothstep ease-in-out: t²(3−2t)
        ease = t * t * (3 - 2 * t)
        rely = START + (TARGET - START) * ease
        self._mic_container.place(relx=0.5, rely=rely, anchor='n')
        self.after(INTERVAL, lambda: self._animate_mic_up(step + 1))

    def _set_result(self, text: str):
        is_first = not self._history
        self._history.insert(0, text)
        self._history = self._history[:10]
        if is_first:
            self._render_history()
        else:
            self._add_history_card(text)

    def _render_history(self):
        for w in self._history_widgets:
            w.destroy()
        self._history_widgets.clear()

        # 首次出現歷史記錄時，觸發 mic 區塊向上滑動動畫
        if self._history and self._mic_centered:
            self._animate_mic_up()

        for i, item in enumerate(self._history):
            text_color = '#F4F4F5' if i == 0 else '#A1A1AA'
            card = self._build_history_card(item, text_color)
            card.grid(row=i, column=0, sticky='ew', pady=(0, 8))
            self._history_widgets.append(card)

    def _build_history_card(self, text: str, text_color: str) -> ctk.CTkFrame:
        """建立單張歷史記錄卡片（不加入 grid，由呼叫端負責排版）"""
        font_family = "Microsoft JhengHei UI"
        card = ctk.CTkFrame(self._result_scroll, fg_color='#27272A', corner_radius=12)
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=0)
        label = ctk.CTkLabel(
            card, text=text, wraplength=270, justify='left',
            font=ctk.CTkFont(family=font_family, size=14),
            text_color=text_color, anchor='w',
        )
        label.grid(row=0, column=0, sticky='nsew', padx=(14, 4), pady=8)
        btn = ctk.CTkButton(
            card, text='複製', width=52, height=28, corner_radius=6,
            fg_color='#3F3F46', hover_color='#52525B',
            font=ctk.CTkFont(family=font_family, size=13),
            command=lambda c=card: self._copy_history_card(c),
        )
        btn.grid(row=0, column=1, sticky='ne', padx=(0, 10), pady=8)
        return card

    def _add_history_card(self, text: str):
        """增量插入新卡片至頂部，現有卡片往下移"""
        # 舊第一筆改為暗色
        if self._history_widgets:
            children = self._history_widgets[0].winfo_children()
            if children:
                children[0].configure(text_color='#A1A1AA')
        # 現有卡片全部往下移一格
        for i, card in enumerate(self._history_widgets):
            card.grid(row=i + 1)
        # 超出 10 筆：直接移除最後一張
        if len(self._history_widgets) >= 10:
            self._history_widgets.pop().destroy()
        # 建立新卡片，插入 list 最前面，直接顯示於 row 0
        new_card = self._build_history_card(text, '#F4F4F5')
        self._history_widgets.insert(0, new_card)
        new_card.grid(row=0, column=0, sticky='ew', pady=(0, 8))

    def _copy_history_card(self, card: ctk.CTkFrame):
        """動態查找卡片位置後複製對應歷史記錄"""
        try:
            self._copy_history(self._history_widgets.index(card))
        except ValueError:
            pass

    def _copy_history(self, idx: int):
        if idx < len(self._history):
            self.clipboard_clear()
            self.clipboard_append(self._history[idx])
            btn = self._history_widgets[idx].winfo_children()[1]
            btn.configure(text='✓')
            self.after(1200, lambda b=btn: b.configure(text='複製'))

    def _hotkey_display(self) -> str:
        hk = self._cfg.get('hotkey', 'alt+`')
        hk_comma = self._cfg.get('hotkey_comma', 'insert')
        return f'快捷鍵：{hk.upper()}（句號） / {hk_comma.upper()}（逗號）'

    # ── 快捷鍵 ────────────────────────────────────────────────────────────────

    def _register_hotkey(self):
        global _hotkey_handle
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

        # 移除先前為 insert 特製的 hook（若有的話）
        comma_hook_remove = getattr(self, '_comma_hook_remove', None)
        if comma_hook_remove:
            try:
                comma_hook_remove()
            except Exception:
                pass
            self._comma_hook_remove = None

        hotkey = self._cfg.get('hotkey', 'alt+`')
        hotkey_comma = self._cfg.get('hotkey_comma', 'insert')
        try:
            def _parse_hk(hk_str):
                parts = [p.strip().lower() for p in hk_str.split('+')]
                mods = [p for p in parts if p in self._MODIFIERS]
                main_key = next((p for p in reversed(parts) if p not in self._MODIFIERS), None)
                return mods, main_key

            hk_mods, hk_main = _parse_hk(hotkey)
            hc_mods, hc_main = _parse_hk(hotkey_comma)

            # 部分按鍵因 scan code 與其他鍵相同，add_hotkey 會誤觸：
            #   insert  (scan 82) ← 小鍵盤 0（NumLock OFF 時）
            #   pause   (scan 69) ← NumLock（兩者 scan code 皆為 69）
            # 凡 main_key 屬於這些鍵，改用 keyboard.hook 檢查 event.name 精確匹配。
            _NAME_HOOK_KEYS = {'insert', 'pause'}
            name_triggers = []  # list of (mods, expected_name, punct)

            if hk_main in _NAME_HOOK_KEYS:
                name_triggers.append((hk_mods, hk_main, '。'))
            else:
                keyboard.add_hotkey(hotkey, lambda: self.after(0, lambda: self._toggle_recording('。')))  # type: ignore[arg-type]

            if hc_main in _NAME_HOOK_KEYS:
                name_triggers.append((hc_mods, hc_main, '，'))
            else:
                keyboard.add_hotkey(hotkey_comma, lambda: self.after(0, lambda: self._toggle_recording('，')))  # type: ignore[arg-type]

            if name_triggers:
                def _on_insert_hook(event, _triggers=name_triggers):
                    if event.event_type != keyboard.KEY_DOWN:
                        return
                    name = event.name.lower() if event.name else ''
                    if not name:
                        return
                    # DEBUG：記錄所有 KEY_DOWN 事件
                    try:
                        import pathlib
                        _log = pathlib.Path(__file__).parent / 'key_debug.log'
                        with open(_log, 'a', encoding='utf-8') as _f:
                            _f.write(f'name={event.name!r} scan={getattr(event,"scan_code",None)} is_keypad={getattr(event,"is_keypad",None)} flags={getattr(event,"flags",None)}\n')
                    except Exception:
                        pass
                    for mods, expected_name, punct in _triggers:
                        if name == expected_name and all(keyboard.is_pressed(m) for m in mods):
                            p = punct
                            self.after(0, lambda p=p: self._toggle_recording(p))
                            break

                self._comma_hook_remove = keyboard.hook(_on_insert_hook)

            _debug_print(f'[main][{now_str()}] ✅ 快捷鍵 {hotkey}（句號）、{hotkey_comma}（逗號）已註冊')
        except Exception as e:
            _debug_print(f'[main][{now_str()}] ❌ 快捷鍵註冊失敗: {e}')

        # 歷史識別快捷鍵：用 Win32 RegisterHotKey 確保按鍵完全攔截不穿透
        self._register_history_hotkeys()

    # ── Win32 RegisterHotKey（記憶快捷鍵）────────────────────────────────────

    _HK_BASE_ID = 0xBFF0
    _MOD_MAP = {'alt': 0x0001, 'ctrl': 0x0002, 'control': 0x0002, 'shift': 0x0004}

    @staticmethod
    def _key_to_vk(name: str) -> int:
        k = name.lower().strip()
        if len(k) == 1 and k.isdigit():
            return ord(k)
        if len(k) == 1 and k.isalpha():
            return ord(k.upper())
        if k.startswith('f') and k[1:].isdigit():
            return 0x6F + int(k[1:])
        return {'space': 0x20, 'enter': 0x0D, 'tab': 0x09, 'pause': 0x13,
                'escape': 0x1B, 'backspace': 0x08, 'delete': 0x2E}.get(k, 0)

    def _parse_hotkey_win32(self, hk_str: str):
        parts = [p.strip().lower() for p in hk_str.split('+')]
        mods, vk = 0, 0
        for p in parts:
            if p in self._MOD_MAP:
                mods |= self._MOD_MAP[p]
            else:
                vk = self._key_to_vk(p)
        return mods, vk

    def _register_history_hotkeys(self):
        from ctypes import wintypes

        # 停止舊的監聽執行緒
        old_tid = getattr(self, '_hk_thread_id', 0)
        old_thread = getattr(self, '_hk_thread', None)
        if old_thread and old_thread.is_alive() and old_tid:
            ctypes.windll.user32.PostThreadMessageW(old_tid, 0x0012, 0, 0)  # WM_QUIT
            old_thread.join(timeout=1.0)
        self._hk_thread_id = 0

        _default_hks = ['alt+shift+1', 'alt+shift+2', 'alt+shift+3', 'alt+shift+4', 'alt+shift+5']
        history_hotkeys = self._cfg.get('history_hotkeys', _default_hks)
        parsed = []
        for i in range(5):
            hk = history_hotkeys[i] if i < len(history_hotkeys) else _default_hks[i]
            parsed.append(self._parse_hotkey_win32(hk) if hk else (0, 0))

        app = self

        def _listener():
            user32 = ctypes.windll.user32
            user32.RegisterHotKey.restype = ctypes.c_bool
            user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
            app._hk_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            ok_count = 0
            for i, (mods, vk) in enumerate(parsed):
                if not vk:
                    continue
                if user32.RegisterHotKey(None, app._HK_BASE_ID + i, mods | 0x4000, vk):
                    ok_count += 1
                else:
                    _debug_print(f'[main][{now_str()}] ❌ Win32 記憶快捷鍵 {i + 1} 註冊失敗 (mods=0x{mods:X} vk=0x{vk:X})')
            _debug_print(f'[main][{now_str()}] ✅ 記憶快捷鍵 {ok_count}/5 已註冊 (Win32)')

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:  # WM_HOTKEY
                    idx = msg.wParam - app._HK_BASE_ID
                    if 0 <= idx < 5:
                        app.after(0, app._paste_history, idx)

            for i in range(5):
                user32.UnregisterHotKey(None, app._HK_BASE_ID + i)

        self._hk_thread = threading.Thread(target=_listener, daemon=True, name='HotkeyListener')
        self._hk_thread.start()

    def _paste_history(self, idx: int):
        """用 Alt+Shift+1~5 貼上對應記憶"""
        if idx < len(self._history):
            text = self._history[idx]
            _debug_print(f'[main][{now_str()}] 📋 貼上記憶 {idx + 1}: "{text[:20]}"')
            paster.paste_text(text, delay_ms=30)
        else:
            _debug_print(f'[main][{now_str()}] ⚠️ 記憶 {idx + 1} 不存在')

    # ── 系統列 ────────────────────────────────────────────────────────────────

    def _start_tray(self):
        if not os.path.exists(ICON_PATH):
            return

        base = Image.open(ICON_PATH).convert('RGBA')
        self._tray_icon_idle = base

        # 錄音狀態：只換背景底色為紅色，白色圖示保留
        def _recolor_background(src: Image.Image, bg_color: tuple) -> Image.Image:
            img = src.copy()
            data = img.getdata()
            new_data = []
            for r, g, b, a in data:  # type: ignore[misc]
                # 透明：保留；接近白色：保留；其餘（背景）：換成目標色
                if a < 10 or (r > 200 and g > 200 and b > 200):
                    new_data.append((r, g, b, a))
                else:
                    new_data.append((*bg_color, a))
            img.putdata(new_data)
            return img

        self._tray_icon_recording = _recolor_background(base, (220, 38, 38))

        def show_window(icon, item):
            self.after(0, self._show_from_tray)

        def quit_app(icon, item):
            icon.stop()
            self.after(0, lambda: (self._save_geometry(), self.destroy()))

        menu = pystray.Menu(
            pystray.MenuItem('開啟視窗', show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出', quit_app),
        )
        self._tray = pystray.Icon('AIWhisper', self._tray_icon_idle, 'AI Whisper', menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _set_tray_icon(self, state: str):
        """切換系統列圖示：idle / recording"""
        if not hasattr(self, '_tray'):
            return
        if state == 'recording':
            self._tray.icon = self._tray_icon_recording
        else:
            self._tray.icon = self._tray_icon_idle

    def _show_from_tray(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _on_configure(self, event):
        """視窗移動或縮放時觸發，debounce 1 秒後存入 config.json"""
        if event.widget is not self:
            return
        if self._geo_save_job:
            self.after_cancel(self._geo_save_job)
        self._geo_save_job = self.after(1000, self._save_geometry)

    def _save_geometry(self):
        self._geo_save_job = None
        settings.save({'geometry': self.geometry()})

    def _on_close(self):
        """關閉視窗時縮到系統列，並儲存位置"""
        self._save_geometry()
        self.withdraw()


# ═════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    app = App()
    app.mainloop()
