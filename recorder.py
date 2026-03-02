# 功能：麥克風錄音
# 職責：用 sounddevice 串流錄音，停止後輸出 WAV bytes；含分段式語音活動偵測（VAD）
# 依賴：sounddevice, numpy, wave, io, threading

import io
import threading
import wave

import numpy as np
import sounddevice as sd


def _safe_print(msg: str):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))


SAMPLE_RATE = 16000
CHANNELS = 1

# ── 語音活動偵測（VAD）參數 ────────────────────────────────────────────────────
# 每幀長度（秒）：30ms 為語音偵測常用基準
VAD_FRAME_SEC = 0.03
# 每幀的 RMS 閾值；int16 ±32768，安靜房間背景雜訊約 100~300，輕聲說話約 500+
VAD_FRAME_THRESHOLD = 300
# 至少需要多少比例的幀超過閾值，才認定為有效語音（0~1）
VAD_SPEECH_RATIO = 0.08
# 最短有效錄音長度（秒），太短視為誤觸
MIN_DURATION_SEC = 0.5


def _has_speech(audio: np.ndarray) -> bool:
    """分段式 VAD：把音訊切成 30ms 幀，計算有語音能量的幀比例。"""
    frame_len = int(SAMPLE_RATE * VAD_FRAME_SEC)
    samples = audio.flatten().astype(np.float32)
    n_frames = len(samples) // frame_len
    if n_frames == 0:
        return False

    frames = samples[:n_frames * frame_len].reshape(n_frames, frame_len)
    rms_per_frame = np.sqrt(np.mean(frames ** 2, axis=1))
    speech_frames = int(np.sum(rms_per_frame > VAD_FRAME_THRESHOLD))
    ratio = speech_frames / n_frames
    _safe_print(
        f'[recorder][VAD] 語音幀 {speech_frames}/{n_frames} ({ratio:.1%})，'
        f'閾值 {VAD_FRAME_THRESHOLD}，最低比例 {VAD_SPEECH_RATIO:.0%}'
    )
    return ratio >= VAD_SPEECH_RATIO


class Recorder:
    def __init__(self):
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        """開始錄音，回傳是否成功"""
        with self._lock:
            if self._recording:
                return False
            self._frames = []
            self._recording = True

        def _callback(indata, frames, time, status):
            if self._recording:
                self._frames.append(indata.copy())

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype='int16',
                callback=_callback,
            )
            self._stream.start()
            return True
        except Exception as e:
            _safe_print(f'[recorder][start] ❌ 錄音裝置錯誤: {e}')
            self._recording = False
            return False

    def stop(self) -> bytes | None:
        """停止錄音並回傳 WAV bytes，若無音訊回傳 None"""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if not self._frames:
            return None

        audio_data = np.concatenate(self._frames, axis=0)

        # 最短錄音長度檢查
        duration = len(audio_data) / SAMPLE_RATE
        if duration < MIN_DURATION_SEC:
            _safe_print(f'[recorder][stop] 錄音太短 ({duration:.2f}s)，略過')
            return None

        # 分段式 VAD：偵測是否有足夠的語音幀
        if not _has_speech(audio_data):
            _safe_print('[recorder][stop] ❌ VAD 未偵測到語音，不送出辨識')
            return None

        return self._to_wav_bytes(audio_data)

    def _to_wav_bytes(self, audio_data: np.ndarray) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes per sample
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())
        buf.seek(0)
        return buf.read()

    @property
    def is_recording(self) -> bool:
        return self._recording
