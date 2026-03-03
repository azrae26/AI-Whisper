# 功能：自動貼上
# 職責：將文字寫入剪貼簿，延遲後模擬 Ctrl+V 貼到當前游標位置；游標在文字最後面時自動補句號或逗號（由 end_prefix 決定）
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


def _set_clipboard_ctypes(text: str) -> bool:
    """使用 ctypes 直接呼叫 Windows API 寫入剪貼簿，可從任意執行緒安全呼叫"""
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    user32   = ctypes.windll.user32    # type: ignore[attr-defined]
    # 64-bit Windows：HANDLE / HGLOBAL / LPVOID 都是指標大小，必須明確宣告 argtypes + restype
    kernel32.GlobalAlloc.argtypes  = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype   = ctypes.c_void_p
    kernel32.GlobalLock.argtypes   = [ctypes.c_void_p]
    kernel32.GlobalLock.restype    = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes   = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = (text + '\0').encode('utf-16-le')
    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        user32.CloseClipboard()


def _is_cursor_at_end() -> bool:
    """用 UI Automation 判斷游標是否在文字最後面（零鍵盤操作、零游標移動）"""
    try:
        focused = auto.GetFocusedControl()
        if not focused:
            _safe_print(f'[paster][{_now()}] ⚠️ 無焦點控件')
            return False

        # 取得文字內容
        try:
            vp = focused.GetValuePattern()  # type: ignore[attr-defined]
            text = vp.Value or ''
        except Exception:
            text = ''

        if not text:
            _safe_print(f'[paster][{_now()}] 📏 [UIA] 文字為空 → 不加句號')
            return False

        # 用 TextPattern 判斷游標是否在最後面
        # 策略：取「游標→文件結尾」的文字，若為空 = 游標在最後
        try:
            tp = focused.GetTextPattern()  # type: ignore[attr-defined]
            doc_range = tp.DocumentRange
            sel = tp.GetSelection()  # 回傳 list[TextRange]

            if not sel:
                _safe_print(f'[paster][{_now()}] ⚠️ [UIA] GetSelection 為空')
                return False

            caret = sel[0]

            # 複製文件範圍，把 Start 移到游標 End → 得到「游標之後的文字」
            after_range = doc_range.Clone()
            after_range.MoveEndpointByRange(0, caret, 1)  # Start → caret.End
            text_after = after_range.GetText(-1)

            at_end = (len(text_after) == 0)
            _safe_print(f'[paster][{_now()}] 📏 [UIA] text={repr(text[:20])}, text_after={repr(text_after[:20])}, at_end={at_end}')
            return at_end
        except Exception as e:
            _safe_print(f'[paster][{_now()}] ⚠️ [UIA] TextPattern 不支援: {e}')
            return False

    except Exception as e:
        _safe_print(f'[paster][{_now()}] ⚠️ [UIA] 錯誤: {e}')
        return False


# ── 游標位置預取 ─────────────────────────────────────────────────────────────
# 錄音結束時預先偵測游標是否在文字最後面，API 回傳後直接使用，省去 ~500ms UIA 延遲

_prefetch_lock = threading.Lock()
_prefetch_result: tuple | None = None  # (perf_counter timestamp, at_end bool)


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
            at_end = _is_cursor_at_end()
            with _prefetch_lock:
                global _prefetch_result
                _prefetch_result = (time.perf_counter(), at_end)
            _safe_print(f'[paster][{_now()}] 🔮 預取游標位置: at_end={at_end} (delay={prefetch_delay:.2f}s, est_api={estimated_api:.2f}s)')
        finally:
            comtypes.CoUninitialize()
    threading.Thread(target=_do_prefetch, daemon=True, name='UIA-Prefetch').start()


def _consume_prefetch(max_age: float = 10.0):
    """取出預取結果（消耗式），超過 max_age 秒視為過期回傳 None"""
    with _prefetch_lock:
        global _prefetch_result
        if _prefetch_result is None:
            return None
        ts, at_end = _prefetch_result
        _prefetch_result = None
        if time.perf_counter() - ts > max_age:
            return None
        return at_end


def _execute_paste(text: str, delay_ms: int, t_received: float, end_prefix: str = '。') -> None:
    """在持久化 worker thread 內執行，COM 已預先初始化；end_prefix：游標在文字最後時加在辨識內容前的符號（句號或逗號）"""
    prefetched = _consume_prefetch()

    if prefetched is not None:
        at_end = prefetched
        if delay_ms > 0:
            time.sleep(delay_ms / 1000)
        _safe_print(f'[paster][{_now()}] 🎯 PASTE: at_end={at_end} (prefetched), prefix={repr(end_prefix)}, final={repr(text[:40])}')
    else:
        t0 = time.perf_counter()
        at_end = _is_cursor_at_end()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        remaining = delay_ms - elapsed_ms
        if remaining > 0:
            time.sleep(remaining / 1000)
        _safe_print(f'[paster][{_now()}] 🎯 PASTE: at_end={at_end}, prefix={repr(end_prefix)}, uia={elapsed_ms:.0f}ms, final={repr(text[:40])}')

    final_text = (end_prefix + text) if at_end else text

    _set_clipboard_ctypes(final_text)
    keyboard.send('ctrl+v')

    if t_received:
        _safe_print(f'[paster][{_now()}] ⏱️ 收到→貼上完成: {time.perf_counter() - t_received:.2f}s')


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
