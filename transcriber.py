# 功能：語音辨識
# 職責：將 WAV bytes 送至 OpenAI Audio Transcriptions API，回傳繁體中文辨識文字（中英混合友好）
# 依賴：openai, opencc

import io
import time

from openai import OpenAI
from opencc import OpenCC

_s2t = OpenCC('s2t')

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
    如果我說日期請用數字表示，不要用中文表示，例如：2026年3月4日、或24年
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
    return text
