# 功能：錄音波形浮動視窗
# 職責：錄音期間在滑鼠所在螢幕底部中央顯示即時音頻波形（漸層透明背景）；辨識時顯示脈衝文字
# 依賴：tkinter, ctypes, PIL, numpy, math, time

import ctypes
import ctypes.wintypes
import datetime
import math
import time
import tkinter as tk

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

# ── GDI / User32 函式簽章（64-bit 指標安全）──────────────────────────────────
_gdi32 = ctypes.windll.gdi32
_user32 = ctypes.windll.user32

_user32.GetDC.argtypes = [ctypes.wintypes.HWND]
_user32.GetDC.restype = ctypes.c_void_p
_user32.ReleaseDC.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]
_user32.ReleaseDC.restype = ctypes.c_int

_gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
_gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint]
_gdi32.CreateDIBSection.restype = ctypes.c_void_p
_gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi32.SelectObject.restype = ctypes.c_void_p
_gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi32.DeleteObject.restype = ctypes.wintypes.BOOL
_gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
_gdi32.DeleteDC.restype = ctypes.wintypes.BOOL

# UpdateLayeredWindow: HWND, HDC, POINT*, SIZE*, HDC, POINT*, COLORREF, BLEND*, DWORD
_user32.UpdateLayeredWindow.argtypes = [
    ctypes.wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.c_void_p,
    ctypes.wintypes.DWORD,
]
_user32.UpdateLayeredWindow.restype = ctypes.wintypes.BOOL

_user32.SetWindowPos.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.wintypes.UINT]
_user32.SetWindowPos.restype = ctypes.wintypes.BOOL
_user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.GetParent.argtypes = [ctypes.wintypes.HWND]
_user32.GetParent.restype = ctypes.wintypes.HWND
_user32.GetCursorPos.argtypes = [ctypes.c_void_p]
_user32.GetCursorPos.restype = ctypes.wintypes.BOOL
_user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.wintypes.DWORD]
_user32.MonitorFromPoint.restype = ctypes.c_void_p
_user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL

# ── 視覺參數 ──────────────────────────────────────────────────────────────────
BAR_COUNT = 35
BAR_WIDTH = 4
BAR_GAP = 2
_BAR_PITCH = BAR_WIDTH + BAR_GAP
_BARS_TOTAL_W = BAR_COUNT * _BAR_PITCH - BAR_GAP
_PAD_X = 16
_PAD_Y = 12
_CANVAS_H = 48
_WIN_W = _BARS_TOTAL_W + _PAD_X * 2
_WIN_H = _CANVAS_H + _PAD_Y * 2
_CORNER_R = 14
_MARGIN_BOTTOM = 80

_BG_RGB = (15, 15, 35)
_BAR_RGBA = (34, 211, 238, 230)
_BAR_HOT_RGBA = (103, 232, 249, 240)
_BAR_MIN_H = 2
_MAX_ALPHA = 153  # 60%

_PROC_DIM = (34, 211, 238)
_PROC_BRIGHT = (103, 232, 249)


def _now():
    return datetime.datetime.now().strftime('%H:%M:%S')


def _safe_print(msg: str):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))


# ── Win32 結構 ────────────────────────────────────────────────────────────────

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
        ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
        ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
        ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
        ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
        ('biClrImportant', ctypes.c_uint32),
    ]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ('BlendOp', ctypes.c_ubyte), ('BlendFlags', ctypes.c_ubyte),
        ('SourceConstantAlpha', ctypes.c_ubyte), ('AlphaFormat', ctypes.c_ubyte),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


# ── 字體 ─────────────────────────────────────────────────────────────────────
try:
    _FONT = ImageFont.truetype("msjhbd.ttc", 18)
except Exception:
    try:
        _FONT = ImageFont.truetype("msjh.ttc", 18)
    except Exception:
        _FONT = ImageFont.load_default()


class WaveformOverlay:
    """錄音時的即時音頻波形覆蓋視窗（漸層透明背景，per-pixel alpha）"""

    def __init__(self, parent):
        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)
        self._win.configure(bg='black')
        self._win.geometry(f'{_WIN_W}x{_WIN_H}')
        self._win.withdraw()

        self._hwnd = None
        self._layered_ready = False
        self._processing = False
        self._proc_job = None
        self._pos_x = 0
        self._pos_y = 0

        self._gradient_bg = self._build_gradient_bg()

    def _ensure_layered(self):
        """確保 Win32 分層視窗已初始化（需在視窗可見後呼叫）"""
        if self._layered_ready:
            return

        try:
            # 方法 1：wm_frame()
            frame_str = self._win.wm_frame()
            if frame_str and frame_str != '0x0':
                self._hwnd = int(frame_str, 16)
            else:
                # 方法 2：GetParent(winfo_id())
                self._hwnd = _user32.GetParent(self._win.winfo_id())

            if not self._hwnd:
                _safe_print(f'[waveform][{_now()}] HWND not found')
                return

            GWL_EXSTYLE = -20
            style = _user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
            style |= 0x00000080  # WS_EX_TOOLWINDOW
            style |= 0x08000000  # WS_EX_NOACTIVATE
            style |= 0x00080000  # WS_EX_LAYERED
            style |= 0x00000020  # WS_EX_TRANSPARENT
            _user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, style)

            # 設定 WS_EX_LAYERED 後必須重新指定 TOPMOST
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            _user32.SetWindowPos(
                self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )

            self._layered_ready = True
            _safe_print(f'[waveform][{_now()}] Layered window ready, hwnd=0x{self._hwnd:X}')
        except Exception as e:
            _safe_print(f'[waveform][{_now()}] _ensure_layered failed: {e}')

    # ── 預建漸層背景 ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_gradient_bg() -> Image.Image:
        """建立圓角漸層背景：上透明 → 中間 80% 不透明 → 下透明（smoothstep）"""
        arr = np.zeros((_WIN_H, _WIN_W, 4), dtype=np.uint8)
        arr[:, :, :3] = _BG_RGB

        mid = _WIN_H / 2
        for y in range(_WIN_H):
            t = y / mid if y < mid else (_WIN_H - 1 - y) / mid
            t = max(0.0, min(1.0, t))
            t = t * t * (3 - 2 * t)  # smoothstep
            arr[y, :, 3] = int(t * _MAX_ALPHA)

        img = Image.fromarray(arr, 'RGBA')

        # 圓角遮罩
        mask = Image.new('L', (_WIN_W, _WIN_H), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [(0, 0), (_WIN_W - 1, _WIN_H - 1)], radius=_CORNER_R, fill=255
        )
        r, g, b, a = img.split()
        a = ImageChops.multiply(a, mask)
        return Image.merge('RGBA', (r, g, b, a))

    # ── 顯示 / 隱藏 ──────────────────────────────────────────────────────────

    def show(self):
        """定位到滑鼠所在螢幕底部中央並顯示"""
        self._processing = False
        left, _top, right, bottom = _get_cursor_monitor_work_area()
        self._pos_x = left + (right - left - _WIN_W) // 2
        self._pos_y = bottom - _WIN_H - _MARGIN_BOTTOM
        self._win.geometry(f'{_WIN_W}x{_WIN_H}+{self._pos_x}+{self._pos_y}')
        self._win.deiconify()
        self._win.lift()
        self._win.update_idletasks()

        # 視窗可見後才初始化分層樣式
        self._ensure_layered()

        # 每次 show 都強制置頂（避免被其他視窗蓋住）
        if self._hwnd:
            _user32.SetWindowPos(
                self._hwnd, -1, 0, 0, 0, 0,  # HWND_TOPMOST
                0x0002 | 0x0001 | 0x0010  # SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )

        self._blit(self._gradient_bg)

    def hide(self):
        """隱藏並停止所有動畫"""
        self._processing = False
        if self._proc_job:
            self._win.after_cancel(self._proc_job)
            self._proc_job = None
        self._win.withdraw()

    # ── 波形繪製 ──────────────────────────────────────────────────────────────

    def update(self, levels: list[float]):
        """以 0~1 浮點陣列更新波形條（鏡像，從中心往上下延伸）"""
        frame = self._gradient_bg.copy()
        draw = ImageDraw.Draw(frame)

        data = levels[-BAR_COUNT:]
        if len(data) < BAR_COUNT:
            data = [0.0] * (BAR_COUNT - len(data)) + data

        mid = _WIN_H / 2
        max_half = _CANVAS_H / 2 - 4

        for i, lv in enumerate(data):
            x0 = _PAD_X + i * _BAR_PITCH
            h = max(_BAR_MIN_H, int(lv * max_half))
            color = _BAR_HOT_RGBA if lv > 0.6 else _BAR_RGBA
            draw.rectangle(
                [(x0, int(mid - h)), (x0 + BAR_WIDTH, int(mid + h))],
                fill=color,
            )

        self._blit(frame)

    # ── 識別中動畫 ────────────────────────────────────────────────────────────

    def show_processing(self):
        """切換為「識別中…」脈衝文字動畫（2 倍速）"""
        self._processing = True
        self._proc_start = time.time()
        self._tick_processing()

    def _tick_processing(self):
        if not self._processing:
            return

        frame = self._gradient_bg.copy()
        draw = ImageDraw.Draw(frame)

        elapsed = time.time() - self._proc_start
        t = (math.sin(elapsed * 2 * math.pi) + 1) / 2

        r = int(_PROC_DIM[0] + (_PROC_BRIGHT[0] - _PROC_DIM[0]) * t)
        g = int(_PROC_DIM[1] + (_PROC_BRIGHT[1] - _PROC_DIM[1]) * t)
        b = int(_PROC_DIM[2] + (_PROC_BRIGHT[2] - _PROC_DIM[2]) * t)

        draw.text(
            (_WIN_W / 2, _WIN_H / 2),
            '識別中…', font=_FONT, fill=(r, g, b, 240), anchor='mm',
        )

        self._blit(frame)
        self._proc_job = self._win.after(33, self._tick_processing)

    # ── Win32 Per-Pixel Alpha 渲染 ────────────────────────────────────────────

    def _blit(self, img: Image.Image):
        """用 UpdateLayeredWindow 將 PIL RGBA 影像渲染到分層視窗"""
        if not self._hwnd:
            _safe_print(f'[waveform][{_now()}] _blit skipped: no hwnd')
            return

        w, h = img.size

        hdc_screen = None
        hdc_mem = None
        hbmp = None
        old_bmp = None

        try:
            # RGBA → 預乘 BGRA（UpdateLayeredWindow 要求）
            arr = np.array(img)
            alpha = arr[:, :, 3].astype(np.float32) / 255.0
            bgra = np.empty((h, w, 4), dtype=np.uint8)
            bgra[:, :, 0] = (arr[:, :, 2] * alpha).astype(np.uint8)  # B
            bgra[:, :, 1] = (arr[:, :, 1] * alpha).astype(np.uint8)  # G
            bgra[:, :, 2] = (arr[:, :, 0] * alpha).astype(np.uint8)  # R
            bgra[:, :, 3] = arr[:, :, 3]                              # A

            hdc_screen = _user32.GetDC(None)
            hdc_mem = _gdi32.CreateCompatibleDC(hdc_screen)

            bmi = _BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h  # top-down DIB
            bmi.biPlanes = 1
            bmi.biBitCount = 32

            ppvBits = ctypes.c_void_p()
            hbmp = _gdi32.CreateDIBSection(
                hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(ppvBits), None, 0
            )
            if not hbmp:
                _safe_print(f'[waveform][{_now()}] CreateDIBSection failed')
                return

            old_bmp = _gdi32.SelectObject(hdc_mem, hbmp)
            ctypes.memmove(ppvBits, bgra.tobytes(), w * h * 4)

            blend = _BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER + AC_SRC_ALPHA
            pt_src = _POINT(0, 0)
            pt_dst = _POINT(self._pos_x, self._pos_y)
            sz = _SIZE(w, h)

            result = _user32.UpdateLayeredWindow(
                self._hwnd, hdc_screen,
                ctypes.byref(pt_dst), ctypes.byref(sz),
                hdc_mem, ctypes.byref(pt_src),
                0, ctypes.byref(blend), 2,  # ULW_ALPHA
            )
            if not result:
                err = ctypes.GetLastError()
                _safe_print(f'[waveform][{_now()}] UpdateLayeredWindow failed, error={err}')
        except Exception as e:
            _safe_print(f'[waveform][{_now()}] _blit error: {e}')
        finally:
            if old_bmp and hdc_mem:
                _gdi32.SelectObject(hdc_mem, old_bmp)
            if hbmp:
                _gdi32.DeleteObject(hbmp)
            if hdc_mem:
                _gdi32.DeleteDC(hdc_mem)
            if hdc_screen:
                _user32.ReleaseDC(None, hdc_screen)


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def _get_cursor_monitor_work_area() -> tuple[int, int, int, int]:
    """回傳滑鼠所在螢幕的工作區域 (left, top, right, bottom)"""

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", ctypes.c_ulong),
        ]

    pt = ctypes.wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    hmon = _user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST

    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    _user32.GetMonitorInfoW(hmon, ctypes.byref(mi))

    r = mi.rcWork
    return (r.left, r.top, r.right, r.bottom)
