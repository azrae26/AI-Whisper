# 功能：設定管理
# 職責：讀寫 config.json（API Key、快捷鍵、模型、開機啟動、視窗位置）
# 依賴：json, os, winreg, sys

import json
import os
import sys
import winreg


def _safe_print(msg: str):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
APP_NAME = 'AIWhisper'

DEFAULTS = {
    'apiKey': '',
    'hotkey': 'ctrl+shift+h',
    'model': 'gpt-4o-transcribe',
    'startup': False,
}


def get() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return DEFAULTS.copy()
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for k, v in DEFAULTS.items():
            if k not in data:
                data[k] = v
        return data
    except Exception:
        return DEFAULTS.copy()


def save(updates: dict) -> None:
    current = get()
    current.update(updates)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(current, f, ensure_ascii=False, indent=2)


def set_startup(enabled: bool) -> None:
    """將應用程式加入或移除 Windows 開機啟動（Registry HKCU Run）"""
    key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        )
        if enabled:
            # 打包後 sys.executable 就是 .exe；開發時用 python script.py
            if getattr(sys, '_MEIPASS', None):
                cmd = f'"{sys.executable}"'
            else:
                script = os.path.abspath(__file__).replace('settings.py', 'main.py')
                cmd = f'"{sys.executable}" "{script}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        _safe_print(f'[settings][set_startup] ❌ 錯誤: {e}')


def is_startup_enabled() -> bool:
    """檢查是否已加入開機啟動"""
    key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False
