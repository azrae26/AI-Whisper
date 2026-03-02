# 功能：語音辨識
# 職責：將 WAV bytes 送至 OpenAI Audio Transcriptions API，回傳辨識文字
# 依賴：openai

import io

from openai import OpenAI

SUPPORTED_MODELS = [
    'gpt-4o-transcribe',
    'gpt-4o-mini-transcribe',
    'whisper-1',
]


def transcribe(wav_bytes: bytes, api_key: str, model: str = 'gpt-4o-transcribe') -> str:
    """
    呼叫 OpenAI Whisper API 辨識語音
    model 預設 gpt-4o-transcribe（最強），可改為 whisper-1 相容舊版
    不指定 language，讓模型自動偵測中英混合
    """
    client = OpenAI(api_key=api_key)

    audio_file = io.BytesIO(wav_bytes)

    response = client.audio.transcriptions.create(
        model=model,
        file=('audio.wav', audio_file, 'audio/wav'),
    )
    return response.text.strip()
