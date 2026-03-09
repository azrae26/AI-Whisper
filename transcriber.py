# 功能：語音辨識
# 職責：將 WAV bytes 送至 OpenAI Audio Transcriptions API，回傳繁體中文辨識文字（中英混合友好）
# 依賴：openai, opencc, cn2an

import io
import re
import time

from openai import OpenAI
from opencc import OpenCC

_s2t = OpenCC('s2t')

# 中文數字字元 → 阿拉伯數字（純數字序列用，如年份 二○二六 → 2026）
_ZH_DIGIT_MAP: dict[str, str] = {
    '零': '0', '○': '0', '〇': '0',
    '一': '1', '二': '2', '兩': '2',
    '三': '3', '四': '4', '五': '5',
    '六': '6', '七': '7', '八': '8', '九': '9',
}
# 結構性中文數字字元（出現時代表是「三十五」這類結構性數字）
_ZH_STRUCT_CHARS = frozenset('十百千萬億')
# 比對連續 2 個以上中文數字字元的 pattern
_ZH_NUM_PATTERN = re.compile('[零○〇一二三四五六七八九十百千萬億兩]{2,}')
# 比對緊接年月日季的中文數字（即使只有 1 個字元，如 三月、十日）
_ZH_NUM_UNIT_PATTERN = re.compile('([零○〇一二三四五六七八九十百千萬億兩]+)([年月日季])')


def _zh_num_to_arabic(zh: str) -> str:
    """將一段中文數字字串轉為阿拉伯數字字串；失敗時原樣返回"""
    # 純數字序列（無結構性字元）→ 逐字映射，適用年份、日期、流水號
    if not _ZH_STRUCT_CHARS.intersection(zh):
        mapped = ''.join(_ZH_DIGIT_MAP.get(c, c) for c in zh)
        if mapped.isdigit():
            return mapped
    # 結構性數字 → 用 cn2an（smart 模式容錯高）
    try:
        import cn2an
        normalized = zh.replace('○', '零').replace('〇', '零')
        return str(cn2an.cn2an(normalized, 'smart'))
    except Exception:
        return zh


def _convert_chinese_numbers(text: str) -> str:
    """將文字中的中文數字轉為阿拉伯數字：
    1. 緊接年/月/日/季的中文數字（含單字元）一律轉換
    2. 連續 2 個以上的中文數字字元一律轉換
    """
    # 先處理「數字+年月日季」，避免被下方 pattern 截斷（如 二十三日 要整段轉）
    text = _ZH_NUM_UNIT_PATTERN.sub(
        lambda m: _zh_num_to_arabic(m.group(1)) + m.group(2), text
    )
    # 再處理剩餘 2+ 連續中文數字
    return _ZH_NUM_PATTERN.sub(lambda m: _zh_num_to_arabic(m.group(0)), text)


# Whisper 模型有時輸出罕用異體字、半形標點，在此統一校正
_POST_CORRECTIONS: dict[str, str] = {
    '?': '？',  # 半形 → 全形問號
    '羣': '群',  # 異體字，如 羣組→群組
    '纔': '才',
    '裏': '裡',
    # 臺 → 台（以詞為單位，避免罕見情況誤換）
    '臺灣': '台灣',
    '臺積電': '台積電',
    '臺北': '台北',
    '臺中': '台中',
    '臺南': '台南',
    '臺東': '台東',
    '臺西': '台西',
    '臺大': '台大',
    '臺科大': '台科大',
    '臺師大': '台師大',
    '臺幣': '台幣',
    '舞臺': '舞台',
    '平臺': '平台',
    '講臺': '講台',
    '陽臺': '陽台',
    '機臺': '機台',
    '臺階': '台階',
    '臺詞': '台詞',
    '臺燈': '台燈',
}

SUPPORTED_MODELS = [
    'gpt-4o-transcribe',
    'gpt-4o-mini-transcribe',
    'whisper-1',
]


def transcribe(wav_bytes: bytes, api_key: str, model: str = 'gpt-4o-transcribe') -> str:
    """
    呼叫 OpenAI Whisper API 辨識語音
    model 預設 gpt-4o-transcribe（最強），可改為 whisper-1 相容舊版
    指定 language='zh' 避免短句被誤判為其他語系；中文夾英文單字仍可正確辨識
    如果我說日期或數字，用阿拉伯數字表示，不要用中文表示，例如：2026年3月4日、或24年、或1997
    專有名詞參考列表：
    CoWoS、Chiplet、矽光子
    """
    client = OpenAI(api_key=api_key)

    audio_file = io.BytesIO(wav_bytes)

    _t0 = time.perf_counter()
    response = client.audio.transcriptions.create(
        model=model,
        file=('audio.wav', audio_file, 'audio/wav'),
        language='zh',
        prompt='以下是繁體中文語音，內容可能夾雜英文單字。',
    )
    print(f'[transcriber][{__import__("datetime").datetime.now().strftime("%H:%M:%S")}] ⏱️ API 耗時: {time.perf_counter() - _t0:.2f}s', flush=True)

    text = _s2t.convert(response.text.strip())
    for wrong, correct in _POST_CORRECTIONS.items():
        text = text.replace(wrong, correct)
    text = _convert_chinese_numbers(text)
    return text
