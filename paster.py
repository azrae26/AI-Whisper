# 功能：自動貼上
# 職責：將文字寫入剪貼簿，延遲後模擬 Ctrl+V 貼到當前游標位置；游標在文字最後面時自動補句號
# 依賴：keyboard, tkinter, uiautomation, datetime
# 偵測原理：uiautomation 讀取焦點控件的文字和游標位置，零鍵盤操作

import datetime
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
_paste_lock = threading.Lock()  # 確保同時只有一個貼上操作，避免剪貼簿競爭


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


def paste_text(text: str, delay_ms: int = 50) -> None:
    """
    寫入剪貼簿並模擬 Ctrl+V，在 worker thread 執行避免阻塞 UI
    delay_ms：貼上前最短等待時間（讓焦點切回前景視窗）
    UIA 偵測與 delay 並行：先跑偵測，再只 sleep 剩餘時間
    """
    if not text:
        return

    def _do_paste():
        import comtypes
        comtypes.CoInitialize()
        try:
            t0 = time.perf_counter()

            at_end = _is_cursor_at_end()

            elapsed_ms = (time.perf_counter() - t0) * 1000
            remaining = delay_ms - elapsed_ms
            if remaining > 0:
                time.sleep(remaining / 1000)

            final_text = ('。' + text) if at_end else text
            _safe_print(f'[paster][{_now()}] 🎯 PASTE: at_end={at_end}, uia={elapsed_ms:.0f}ms, final={repr(final_text[:40])}')

            with _paste_lock:
                if _tk_root:
                    _tk_root.clipboard_clear()
                    _tk_root.clipboard_append(final_text)
                    _tk_root.update()
                keyboard.send('ctrl+v')
        finally:
            comtypes.CoUninitialize()

    threading.Thread(target=_do_paste, daemon=True).start()
