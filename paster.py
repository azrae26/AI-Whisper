# 功能：自動貼上
# 職責：將文字寫入 tkinter 剪貼簿，延遲後模擬 Ctrl+V 貼到當前游標位置
# 依賴：keyboard, tkinter（透過傳入 root 使用）

import time
import threading

import keyboard

_tk_root = None


def set_tk_root(root) -> None:
    """傳入 customtkinter/tkinter root 以使用其剪貼簿方法"""
    global _tk_root
    _tk_root = root


def paste_text(text: str, delay_ms: int = 250) -> None:
    """
    寫入剪貼簿並模擬 Ctrl+V，在 worker thread 執行避免阻塞 UI
    delay_ms：貼上前等待時間（讓焦點切回前景視窗）
    """
    if not text:
        return

    def _do_paste():
        if _tk_root:
            # 用 tkinter 內建剪貼簿，不需 pyperclip
            _tk_root.clipboard_clear()
            _tk_root.clipboard_append(text)
            _tk_root.update()

        time.sleep(delay_ms / 1000)
        keyboard.send('ctrl+v')

    threading.Thread(target=_do_paste, daemon=True).start()
