# 功能：自動貼上
# 職責：將文字寫入剪貼簿，延遲後模擬 Ctrl+V 貼到當前游標位置；貼上後還原原本剪貼簿內容；游標在文字最後面時自動補句號或逗號（由 end_prefix 決定）；若最後一字已是標點則不補
# 依賴：keyboard, uiautomation, ctypes, datetime
# 偵測原理：uiautomation 讀取焦點控件的文字和游標位置，零鍵盤操作
# 優化：持久化 paste worker thread（COM 只初始化一次）+ ctypes clipboard（thread-safe）

import ctypes
import datetime
import queue
import sys
import time
import threading

import keyboard
import uiautomation as auto

# 修正 console 編碼，讓中文不會變問號
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
except Exception:
    pass

_tk_root = None
_paste_queue: queue.SimpleQueue = queue.SimpleQueue()


def _now():
    return datetime.datetime.now().strftime('%H:%M:%S')


def _safe_print(msg: str):
    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(msg.encode('utf-8', 'replace').decode('utf-8', 'replace'), flush=True)
        except Exception:
            pass


def set_tk_root(root) -> None:
    """傳入 customtkinter/tkinter root 以使用其剪貼簿方法"""
    global _tk_root
    _tk_root = root


_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def _init_clipboard_api():
    """宣告 ctypes argtypes/restype（僅需呼叫一次）"""
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    user32   = ctypes.windll.user32    # type: ignore[attr-defined]
    kernel32.GlobalAlloc.argtypes  = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype   = ctypes.c_void_p
    kernel32.GlobalLock.argtypes   = [ctypes.c_void_p]
    kernel32.GlobalLock.restype    = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes   = [ctypes.c_void_p]
    kernel32.GlobalSize.argtypes   = [ctypes.c_void_p]
    kernel32.GlobalSize.restype    = ctypes.c_size_t
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype  = ctypes.c_void_p
    user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
    user32.EnumClipboardFormats.restype  = ctypes.c_uint


_init_clipboard_api()


# 游標在最後時，若文字最後一字已是這些標點則不補 end_prefix，避免重複（含中英、全半形、括弧結尾）
_ENDING_PUNCTUATION = frozenset(
    '。，、；：？！. , ; : ? ! …'
    '．，；：？！'  # 全形標點
    '—–-'        # 破折號、連字號
    '·\'"~'      # 間隔號、引號、波浪
)

# 剪貼簿格式中屬於 GDI 物件的 handle，不可使用 GlobalLock，需排除
_CLIPBOARD_GDI_FORMATS = {
    2,   # CF_BITMAP
    3,   # CF_METAFILEPICT
    9,   # CF_PALETTE
    14,  # CF_ENHMETAFILE
}


def _is_hglobal_format(fmt: int) -> bool:
    """判斷此剪貼簿格式的 handle 是否為 HGLOBAL（可安全使用 GlobalLock）"""
    if fmt in _CLIPBOARD_GDI_FORMATS:
        return False
    # GDI 物件範圍 0x0300–0x03FF 也不是 HGLOBAL
    if 0x0300 <= fmt <= 0x03FF:
        return False
    return True


def _save_clipboard_all() -> list[tuple[int, bytes]] | None:
    """備份剪貼簿所有 HGLOBAL 格式的原始資料，回傳 [(format, bytes), ...] 或 None"""
    user32   = ctypes.windll.user32    # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not user32.OpenClipboard(0):
        return None
    try:
        items: list[tuple[int, bytes]] = []
        fmt = user32.EnumClipboardFormats(0)
        while fmt:
            if _is_hglobal_format(fmt):
                h = user32.GetClipboardData(fmt)
                if h:
                    ptr = kernel32.GlobalLock(h)
                    if ptr:
                        try:
                            size = kernel32.GlobalSize(h)
                            if size > 0:
                                items.append((fmt, ctypes.string_at(ptr, size)))
                        finally:
                            kernel32.GlobalUnlock(h)
            fmt = user32.EnumClipboardFormats(fmt)
        return items if items else None
    except Exception as e:
        _safe_print(f'[paster][{_now()}] ⚠️ 備份剪貼簿失敗: {e}')
        return None
    finally:
        user32.CloseClipboard()


def _restore_clipboard_all(items: list[tuple[int, bytes]]) -> bool:
    """從備份資料還原剪貼簿所有格式"""
    user32   = ctypes.windll.user32    # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        for fmt, data in items:
            h = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
            if not h:
                continue
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                kernel32.GlobalFree(h)
                continue
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(h)
            user32.SetClipboardData(fmt, h)
        return True
    except Exception as e:
        _safe_print(f'[paster][{_now()}] ⚠️ 還原剪貼簿失敗: {e}')
        return False
    finally:
        user32.CloseClipboard()


def _set_clipboard_ctypes(text: str) -> bool:
    """使用 ctypes 直接呼叫 Windows API 寫入剪貼簿，可從任意執行緒安全呼叫"""
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    user32   = ctypes.windll.user32    # type: ignore[attr-defined]
    data = (text + '\0').encode('utf-16-le')
    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(_CF_UNICODETEXT, h)
        return True
    finally:
        user32.CloseClipboard()


def _is_cursor_at_end() -> tuple[bool, bool]:
    """用 UI Automation 判斷游標是否在文字最後面；回傳 (at_end, last_char_is_punctuation)"""
    try:
        focused = auto.GetFocusedControl()
        if not focused:
            _safe_print(f'[paster][{_now()}] ⚠️ 無焦點控件')
            return (False, False)

        # 取得文字內容
        try:
            vp = focused.GetValuePattern()  # type: ignore[attr-defined]
            text = vp.Value or ''
        except Exception:
            text = ''

        if not text:
            _safe_print(f'[paster][{_now()}] 📏 [UIA] 文字為空 → 不加句號')
            return (False, False)

        # 用 TextPattern 判斷游標是否在最後面
        # 策略：取「游標→文件結尾」的文字，若為空 = 游標在最後
        try:
            tp = focused.GetTextPattern()  # type: ignore[attr-defined]
            doc_range = tp.DocumentRange
            sel = tp.GetSelection()  # 回傳 list[TextRange]

            if not sel:
                _safe_print(f'[paster][{_now()}] ⚠️ [UIA] GetSelection 為空')
                return (False, False)

            caret = sel[0]

            # 複製文件範圍，把 Start 移到游標 End → 得到「游標之後的文字」
            after_range = doc_range.Clone()
            after_range.MoveEndpointByRange(0, caret, 1)  # Start → caret.End
            text_after = after_range.GetText(-1)

            at_end = (len(text_after) == 0)
            stripped = text.rstrip()
            last_char_is_punctuation = bool(stripped and stripped[-1] in _ENDING_PUNCTUATION)
            _safe_print(f'[paster][{_now()}] 📏 [UIA] text={repr(text[:20])}, text_after={repr(text_after[:20])}, at_end={at_end}, last_punct={last_char_is_punctuation}')
            return (at_end, last_char_is_punctuation)
        except Exception as e:
            _safe_print(f'[paster][{_now()}] ⚠️ [UIA] TextPattern 不支援: {e}')
            return (False, False)

    except Exception as e:
        _safe_print(f'[paster][{_now()}] ⚠️ [UIA] 錯誤: {e}')
        return (False, False)


# ── 游標位置預取 ─────────────────────────────────────────────────────────────
# 錄音結束時預先偵測游標是否在文字最後面，API 回傳後直接使用，省去 ~500ms UIA 延遲

_prefetch_lock = threading.Lock()
_prefetch_result: tuple | None = None  # (perf_counter timestamp, at_end bool, last_char_is_punctuation bool)


def prefetch_cursor_position(wav_bytes_len: int = 0) -> None:
    """
    錄音結束時呼叫，根據音訊大小估算 API 耗時，在 API 即將回傳前才啟動 UIA 偵測
    wav_bytes_len：WAV 原始 bytes 長度，用於估算音訊秒數與 API 耗時
    """
    # 16kHz 16-bit mono → 32000 bytes/sec（加 44 bytes WAV header）
    audio_sec = max(0, (wav_bytes_len - 44)) / 32000 if wav_bytes_len > 44 else 0
    # 實測 API 耗時：≤15s 近似線性，>15s 增長明顯趨緩
    #   10s→0.95s, 12s→1.45s, 15s→1.66s, 21s→1.73s, 32s→2.17s
    if audio_sec <= 15:
        estimated_api = audio_sec * 0.10 + 0.25
    else:
        estimated_api = audio_sec * 0.03 + 1.30
    # UIA 固定 ~500ms，lead_time 只需涵蓋 UIA + 少許餘量，與音訊長度無關
    lead_time = 0.45
    prefetch_delay = max(0, estimated_api - lead_time)

    def _do_prefetch():
        if prefetch_delay > 0:
            time.sleep(prefetch_delay)
        import comtypes
        comtypes.CoInitialize()
        try:
            at_end, last_char_is_punctuation = _is_cursor_at_end()
            with _prefetch_lock:
                global _prefetch_result
                _prefetch_result = (time.perf_counter(), at_end, last_char_is_punctuation)
            _safe_print(f'[paster][{_now()}] 🔮 預取游標位置: at_end={at_end}, last_punct={last_char_is_punctuation} (delay={prefetch_delay:.2f}s, est_api={estimated_api:.2f}s)')
        finally:
            comtypes.CoUninitialize()
    threading.Thread(target=_do_prefetch, daemon=True, name='UIA-Prefetch').start()


def _consume_prefetch(max_age: float = 10.0) -> tuple[bool, bool] | None:
    """取出預取結果（消耗式），超過 max_age 秒視為過期回傳 None；回傳 (at_end, last_char_is_punctuation)"""
    with _prefetch_lock:
        global _prefetch_result
        if _prefetch_result is None:
            return None
        ts, at_end, last_char_is_punctuation = _prefetch_result
        _prefetch_result = None
        if time.perf_counter() - ts > max_age:
            return None
        return (at_end, last_char_is_punctuation)


def _execute_paste(text: str, delay_ms: int, t_received: float, end_prefix: str = '。') -> None:
    """在持久化 worker thread 內執行，COM 已預先初始化；end_prefix：游標在文字最後時加在辨識內容前的符號（句號或逗號）；若最後一字已是標點則不補"""
    prefetched = _consume_prefetch()

    if prefetched is not None:
        at_end, last_char_is_punctuation = prefetched
        if delay_ms > 0:
            time.sleep(delay_ms / 1000)
        add_prefix = at_end and not last_char_is_punctuation
        _safe_print(f'[paster][{_now()}] 🎯 PASTE: at_end={at_end}, last_punct={last_char_is_punctuation}, add_prefix={add_prefix} (prefetched), prefix={repr(end_prefix)}, final={repr(text[:40])}')
    else:
        t0 = time.perf_counter()
        at_end, last_char_is_punctuation = _is_cursor_at_end()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        remaining = delay_ms - elapsed_ms
        if remaining > 0:
            time.sleep(remaining / 1000)
        add_prefix = at_end and not last_char_is_punctuation
        _safe_print(f'[paster][{_now()}] 🎯 PASTE: at_end={at_end}, last_punct={last_char_is_punctuation}, add_prefix={add_prefix}, prefix={repr(end_prefix)}, uia={elapsed_ms:.0f}ms, final={repr(text[:40])}')

    final_text = (end_prefix + text) if add_prefix else text

    # 暫存原本的剪貼簿所有格式（文字、圖片等）
    old_clipboard = _save_clipboard_all()

    cb_ok = _set_clipboard_ctypes(final_text)
    if not cb_ok:
        _safe_print(f'[paster][{_now()}] ❌ [PASTE-FAIL] 剪貼簿寫入失敗，text={repr(final_text[:40])}')

    # 記錄貼上時的前景視窗（診斷「有辨識但沒貼上」問題）
    try:
        _u32 = ctypes.windll.user32  # type: ignore[attr-defined]
        _hwnd = _u32.GetForegroundWindow()
        _buf = ctypes.create_unicode_buffer(128)
        _u32.GetWindowTextW(_hwnd, _buf, 128)
        _win_title = _buf.value
    except Exception:
        _hwnd, _win_title = 0, '(unknown)'
    _safe_print(f'[paster][{_now()}] ⌨️ Ctrl+V 送出，cb_ok={cb_ok}，視窗="{_win_title}"，hwnd={_hwnd:#010x}，text={repr(final_text[:40])}')

    keyboard.send('ctrl+v')

    if t_received:
        _safe_print(f'[paster][{_now()}] ⏱️ 收到→貼上完成: {time.perf_counter() - t_received:.2f}s')

    # 等待 Ctrl+V 完成後還原原本的剪貼簿
    time.sleep(0.40)
    if old_clipboard is not None:
        _restore_clipboard_all(old_clipboard)
        _safe_print(f'[paster][{_now()}] 📋 剪貼簿已還原（{len(old_clipboard)} 種格式）')


def _paste_worker() -> None:
    """持久化 paste 執行緒：COM 初始化一次，透過 queue 接收工作，消除每次貼上的 CoInitialize 開銷"""
    import comtypes
    comtypes.CoInitialize()
    try:
        while True:
            job = _paste_queue.get()
            if job is None:
                break
            text, delay_ms, t_received, end_prefix = job
            _execute_paste(text, delay_ms, t_received, end_prefix)
    finally:
        comtypes.CoUninitialize()


# 模組載入時啟動 worker，全程持續運行
_worker_thread = threading.Thread(target=_paste_worker, daemon=True, name='PasteWorker')
_worker_thread.start()


def paste_text(text: str, delay_ms: int = 50, t_received: float = 0.0, end_prefix: str = '。') -> None:
    """
    將貼上工作推入 queue，由持久化 worker thread 執行（thread-safe，可從任意執行緒呼叫）
    delay_ms：貼上前最短等待時間（讓焦點切回前景視窗）
    t_received：API 回傳瞬間的 perf_counter，用於計算收到→貼上耗時
    end_prefix：游標在文字最後時加在辨識內容前的符號（預設句號。，可改為逗號，）
    """
    if not text:
        return
    _paste_queue.put((text, delay_ms, t_received, end_prefix))
