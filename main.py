# 功能：AI Whisper 語音轉文字主程式
# 職責：UI 管理（主頁面/設定頁面）、系統列常駐、全域快捷鍵、錄音與辨識流程協調
# 依賴：customtkinter, pystray, keyboard, recorder, transcriber, paster, settings

import ctypes
import math
import os
import sys
import threading
import time
import datetime

# 在任何 tkinter 初始化之前，鎖定為 System DPI Awareness
# 跨螢幕移動時由 Windows GPU 做點陣圖縮放，避免 tkinter 逐 widget 重算造成 lag
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
except Exception:
    pass

import customtkinter as ctk
from PIL import Image, ImageTk
import pystray
import keyboard

import settings
import recorder as rec_module
import transcriber
import paster

# ── 路徑 ─────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, 'assets', 'icon.png')

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

        # 設定視窗圖示（需用 PhotoImage，保留參照防 GC）
        if os.path.exists(ICON_PATH):
            try:
                img = Image.open(ICON_PATH).resize((32, 32))
                self._icon_photo = ImageTk.PhotoImage(img)
                self.iconphoto(True, self._icon_photo)  # type: ignore[arg-type]
            except Exception:
                pass

        # 關閉按鈕 -> 縮到系統列
        self.protocol('WM_DELETE_WINDOW', self._on_close)

        # 初始化 paster 剪貼簿橋接
        paster.set_tk_root(self)

        # 讀取設定
        self._cfg = settings.get()

        # 狀態
        self._state = 'idle'  # idle | recording | processing
        self._page = 'main'   # main | settings
        self._anim_dots = 0
        self._anim_job = None
        self._history: list[str] = []  # 最近 5 組辨識結果

        self._build_ui()
        self._register_hotkey()
        self._start_tray()

        # 若無 API Key，啟動後自動開設定頁
        if not self._cfg.get('apiKey'):
            self.after(300, self._show_settings)

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
        frame.grid_rowconfigure(3, weight=1)

        # ── 頂部標題列
        top = ctk.CTkFrame(frame, fg_color='#18181B', corner_radius=0, height=56)
        top.grid(row=0, column=0, sticky='ew')
        top.grid_columnconfigure(0, weight=1)
        top.grid_propagate(False)

        ctk.CTkLabel(
            top, text='◉ AI Whisper', font=ctk.CTkFont(family=font_family, size=18, weight='bold'),
            text_color='#F4F4F5'
        ).grid(row=0, column=0, padx=20, pady=14, sticky='w')

        ctk.CTkButton(
            top, text='•••', width=40, height=40,
            fg_color='transparent', hover_color='#27272A',
            font=ctk.CTkFont(family=font_family, size=16, weight='bold'), text_color='#71717A',
            command=self._show_settings
        ).grid(row=0, column=1, padx=(0, 12), pady=8, sticky='e')

        # ── 麥克風按鈕區
        mic_area = ctk.CTkFrame(frame, fg_color='transparent')
        mic_area.grid(row=1, column=0, pady=(36, 12))

        self._mic_btn = ctk.CTkButton(
            mic_area,
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
            mic_area,
            text=self._hotkey_display(),
            font=ctk.CTkFont(family=font_family, size=12),
            text_color='#71717A',
        )
        self._hotkey_label.pack(pady=(12, 0))

        # ── 狀態標籤
        self._status_label = ctk.CTkLabel(
            frame,
            text='等待中',
            font=ctk.CTkFont(family=font_family, size=14, weight='bold'),
            text_color='#A1A1AA',
        )
        self._status_label.grid(row=2, column=0, pady=(0, 16))

        # ── 結果區（可捲動，每條紀錄含獨立複製按鈕）
        self._result_scroll = ctk.CTkScrollableFrame(
            frame, fg_color='transparent', corner_radius=0,
            scrollbar_button_color='#121212', scrollbar_button_hover_color='#3F3F46',
        )
        self._result_scroll.grid(row=3, column=0, sticky='nsew', padx=(24, 8), pady=(0, 12))
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

        ctk.CTkButton(
            top, text='←', width=40, height=40,
            fg_color='transparent', hover_color='#27272A',
            font=ctk.CTkFont(family=font_family, size=20), text_color='#A1A1AA',
            command=self._show_main,
        ).grid(row=0, column=0, padx=12, pady=8)

        ctk.CTkLabel(
            top, text='設定', font=ctk.CTkFont(family=font_family, size=18, weight='bold'),
            text_color='#F4F4F5'
        ).grid(row=0, column=1, pady=14, sticky='w', padx=4)

        # ── 設定內容捲動區
        scroll = ctk.CTkScrollableFrame(
            frame, fg_color='transparent',
            scrollbar_button_color='#121212', scrollbar_button_hover_color='#3F3F46',
        )
        scroll.grid(row=1, column=0, sticky='nsew', padx=24, pady=16)
        frame.grid_rowconfigure(1, weight=1)
        scroll.grid_columnconfigure(0, weight=1)

        row = 0

        def add_label(text):
            lbl = ctk.CTkLabel(scroll, text=text, font=ctk.CTkFont(family=font_family, size=14, weight='bold'),
                               text_color='#D4D4D8')
            lbl.grid(row=row, column=0, sticky='w', pady=(0, 6))

        # API Key
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

        self._show_key_btn = ctk.CTkButton(
            key_frame, text='👁', width=44, height=40,
            fg_color='#3F3F46', hover_color='#52525B',
            font=ctk.CTkFont(family=font_family, size=16),
            command=self._toggle_key_visibility,
        )
        self._show_key_btn.grid(row=0, column=1, padx=(8, 0))
        self._key_visible = False

        # 辨識模型
        add_label('辨識模型')
        row += 1

        self._model_var = ctk.StringVar(value=self._cfg.get('model', 'gpt-4o-transcribe'))
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

        # 全域快捷鍵
        add_label('全域快捷鍵')
        row += 1

        ctk.CTkLabel(
            scroll, text='點擊按鈕後，按下想要的組合鍵（Esc 取消）',
            font=ctk.CTkFont(family=font_family, size=12), text_color='#71717A',
        ).grid(row=row, column=0, sticky='w', pady=(0, 6))
        row += 1

        self._hotkey_var = ctk.StringVar(value=self._cfg.get('hotkey', 'ctrl+shift+h'))
        self._hotkey_capture_btn = ctk.CTkButton(
            scroll,
            text=self._cfg.get('hotkey', 'ctrl+shift+h').upper(),
            height=40,
            corner_radius=8,
            fg_color='#27272A', hover_color='#3F3F46',
            border_width=1, border_color='#3F3F46',
            font=ctk.CTkFont(family=font_family, size=15, weight='bold'),
            text_color='#F4F4F5',
            command=self._start_hotkey_capture,
        )
        self._hotkey_capture_btn.grid(row=row, column=0, sticky='ew', pady=(0, 20))
        row += 1

        # 開機啟動
        add_label('開機時自動啟動')
        row += 1

        startup_frame = ctk.CTkFrame(scroll, fg_color='transparent')
        startup_frame.grid(row=row, column=0, sticky='ew', pady=(0, 32))
        row += 1

        self._startup_var = ctk.BooleanVar(value=settings.is_startup_enabled())
        ctk.CTkSwitch(
            startup_frame,
            text='',
            variable=self._startup_var,
            onvalue=True, offvalue=False,
            progress_color='#2563EB'
        ).pack(side='left')

        # 儲存按鈕
        ctk.CTkButton(
            scroll,
            text='儲存設定',
            height=44,
            corner_radius=8,
            fg_color='#2563EB', hover_color='#1D4ED8',
            font=ctk.CTkFont(family=font_family, size=15, weight='bold'),
            command=self._save_settings,
        ).grid(row=row, column=0, sticky='ew', pady=(0, 12))

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

    _MODIFIERS = {'ctrl', 'shift', 'alt', 'left ctrl', 'right ctrl',
                   'left shift', 'right shift', 'left alt', 'right alt',
                   'left windows', 'right windows', 'windows'}
    _MOD_NORMALIZE = {
        'left ctrl': 'ctrl', 'right ctrl': 'ctrl',
        'left shift': 'shift', 'right shift': 'shift',
        'left alt': 'alt', 'right alt': 'alt',
        'left windows': 'windows', 'right windows': 'windows',
    }

    def _start_hotkey_capture(self):
        """進入快捷鍵捕捉模式，用 keyboard.hook 追蹤按鍵組合"""
        self._hotkey_capture_btn.configure(
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
        self._hotkey_var.set(hotkey)
        self._hotkey_capture_btn.configure(
            text=hotkey.upper(),
            fg_color='#27272A', border_color='#3F3F46', text_color='#F4F4F5',
        )
        self._register_hotkey()

    def _finish_capture_cancel(self):
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        hk = self._hotkey_var.get()
        self._hotkey_capture_btn.configure(
            text=hk.upper(),
            fg_color='#27272A', border_color='#3F3F46', text_color='#F4F4F5',
        )
        self._register_hotkey()

    def _save_settings(self):
        new_cfg = {
            'apiKey': self._api_key_var.get().strip(),
            'model': self._model_var.get(),
            'hotkey': self._hotkey_var.get().strip().lower(),
            'startup': self._startup_var.get(),
        }
        settings.save(new_cfg)
        settings.set_startup(new_cfg['startup'])
        self._cfg = settings.get()

        # 重新註冊快捷鍵
        self._register_hotkey()
        self._hotkey_label.configure(text=self._hotkey_display())

        self._show_main()
        self._set_status('設定已儲存 ✓', '#10B981')
        self.after(2000, lambda: self._set_status('等待中', '#A1A1AA'))

    # ── 錄音控制 ──────────────────────────────────────────────────────────────

    def _toggle_recording(self):
        if self._state == 'idle':
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
        self._start_anim()
        _debug_print(f'[main][{now_str()}] 🎙️ 開始錄音')

    def _stop_recording(self):
        self._state = 'processing'
        self._stop_anim()
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
        threading.Thread(target=self._run_transcribe, args=(wav_bytes,), daemon=True).start()

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
            text = transcriber.transcribe(wav_bytes, api_key=api_key, model=model)
            _debug_print(f'[main][{now_str()}] ✅ 辨識完成: "{text}"')
            self.after(0, lambda: self._on_transcribe_done(text))
        except Exception as e:
            err_msg = str(e)
            _debug_print(f'[main][{now_str()}] ❌ 辨識失敗: {err_msg}')
            self.after(0, lambda: self._on_transcribe_error(err_msg))

    def _on_transcribe_done(self, text: str):
        self._reset_idle()
        self._set_result(text)
        self._set_status('辨識完成 ✓', '#10B981')
        self.after(2000, lambda: self._set_status('等待中', '#A1A1AA'))

        # 自動貼到游標處
        paster.paste_text(text)

    def _on_transcribe_error(self, err_msg: str):
        self._reset_idle()
        short = err_msg[:60] + '…' if len(err_msg) > 60 else err_msg
        self._set_status(f'❌ {short}', '#EF4444')
        self.after(4000, lambda: self._set_status('等待中', '#A1A1AA'))

    def _reset_idle(self):
        self._state = 'idle'
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
        self._status_label.configure(
            text=f'● 錄音中  {minutes:02d}:{seconds:02d}',
            text_color=color,
        )
        self._mic_btn.configure(border_color=color)

        self._anim_job = self.after(33, self._tick_anim)  # ~30fps

    def _stop_anim(self):
        if self._anim_job:
            self.after_cancel(self._anim_job)
            self._anim_job = None

    # ── UI 更新工具 ───────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = '#A1A1AA'):
        self._status_label.configure(text=text, text_color=color)

    def _set_result(self, text: str):
        self._history.insert(0, text)
        self._history = self._history[:5]
        self._render_history()

    def _render_history(self):
        font_family = "Microsoft JhengHei UI"
        for w in self._history_widgets:
            w.destroy()
        self._history_widgets.clear()

        for i, item in enumerate(self._history):
            card = ctk.CTkFrame(self._result_scroll, fg_color='#27272A', corner_radius=12)
            card.grid(row=i, column=0, sticky='ew', pady=(0, 8))
            card.grid_columnconfigure(0, weight=1)
            card.grid_columnconfigure(1, weight=0)

            text_color = '#F4F4F5' if i == 0 else '#A1A1AA'
            label = ctk.CTkLabel(
                card, text=item, wraplength=270, justify='left',
                font=ctk.CTkFont(family=font_family, size=14),
                text_color=text_color, anchor='nw',
            )
            label.grid(row=0, column=0, sticky='ew', padx=(14, 4), pady=12)

            idx = i
            btn = ctk.CTkButton(
                card, text='複製', width=52, height=28, corner_radius=6,
                fg_color='#3F3F46', hover_color='#52525B',
                font=ctk.CTkFont(family=font_family, size=13),
                command=lambda idx=idx: self._copy_history(idx),
            )
            btn.grid(row=0, column=1, sticky='ne', padx=(0, 10), pady=12)

            self._history_widgets.append(card)

    def _copy_history(self, idx: int):
        if idx < len(self._history):
            self.clipboard_clear()
            self.clipboard_append(self._history[idx])
            btn = self._history_widgets[idx].winfo_children()[1]
            btn.configure(text='✓')
            self.after(1200, lambda b=btn: b.configure(text='複製'))

    def _hotkey_display(self) -> str:
        hk = self._cfg.get('hotkey', 'ctrl+shift+h')
        return f'快捷鍵：{hk.upper()}'

    # ── 快捷鍵 ────────────────────────────────────────────────────────────────

    def _register_hotkey(self):
        global _hotkey_handle
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

        hotkey = self._cfg.get('hotkey', 'ctrl+shift+h')
        try:
            keyboard.add_hotkey(hotkey, lambda: self.after(0, self._toggle_recording))
            _debug_print(f'[main][{now_str()}] ✅ 快捷鍵 {hotkey} 已註冊')
        except Exception as e:
            _debug_print(f'[main][{now_str()}] ❌ 快捷鍵註冊失敗: {e}')

    # ── 系統列 ────────────────────────────────────────────────────────────────

    def _start_tray(self):
        if not os.path.exists(ICON_PATH):
            return

        base = Image.open(ICON_PATH).resize((64, 64)).convert('RGBA')
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
            self.after(0, self.destroy)

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

    def _save_geometry(self):
        settings.save({'geometry': self.geometry()})

    def _on_close(self):
        """關閉視窗時縮到系統列，並儲存位置"""
        self._save_geometry()
        self.withdraw()


# ═════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    app = App()
    app.mainloop()
