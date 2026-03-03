# 功能：語音辨識
# 職責：將 WAV bytes 送至 OpenAI Audio Transcriptions API，回傳繁體中文辨識文字（中英混合友好）
# 依賴：openai, opencc

import io
import time

from openai import OpenAI
from opencc import OpenCC

_s2t = OpenCC('s2t')

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

    return _s2t.convert(response.text.strip())
