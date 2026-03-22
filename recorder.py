# 功能：麥克風錄音
# 職責：用 sounddevice 串流錄音，停止後輸出 WAV bytes；含分段式 VAD、即時波形資料、自動分段送出、預熱機制（stop 後 stream 保持開啟，shutdown 才真正關閉）
# 依賴：sounddevice, numpy, wave, io, threading, datetime, time

import datetime
import io
import time
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
# 靜音偵測閾值：waveform level（RMS/5000）低於此值視為靜音
_SILENCE_LEVEL = 0.06


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
        # 即時波形：儲存最近 N 個 RMS 值（0~1），供 UI 繪製波形
        self._waveform: list[float] = []
        self._wf_lock = threading.Lock()
        # 分段辨識用：記錄本段累積樣本數、連續靜音 chunk 數、每 chunk 樣本數
        self._segment_samples: int = 0
        self._silence_chunks: int = 0
        self._chunk_samples: int = 0
        # 麥克風實際開啟延遲量測
        self._stream_start_time: float = 0.0
        self._first_cb_logged: bool = False

    def start(self) -> bool:
        """開始錄音，回傳是否成功。
        若 stream 已預熱（pre-warmed），直接翻轉 flag，跳過 OS 開裝置延遲。
        """
        with self._lock:
            if self._recording:
                return False
            self._frames = []
            self._waveform = []
            self._segment_samples = 0
            self._silence_chunks = 0
            self._chunk_samples = 0
            self._recording = True

            if self._stream is not None:
                # 預熱命中：stream 已在跑，直接開始收音
                _safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] 🚀 預熱命中，直接開始錄音')
                return True

        # Cold start：需要重新建立 stream
        _perf = time.perf_counter  # 預先捕捉，避免被 _callback 的 time 參數遮蔽
        self._first_cb_logged = False

        def _callback(indata, frames, time, status):
            if not self._first_cb_logged:
                self._first_cb_logged = True
                delay_ms = (_perf() - self._stream_start_time) * 1000
                _safe_print(
                    f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] '
                    f'🎤 第一包音訊到達，麥克風實際開啟延遲 {delay_ms:.1f}ms'
                )
            if self._recording:
                self._frames.append(indata.copy())
                chunk_len = len(indata)
                # 記錄 chunk 大小（第一次才記，之後穩定不變）
                if not self._chunk_samples:
                    self._chunk_samples = chunk_len
                # 即時波形：計算本 chunk 的 RMS 並正規化到 0~1
                rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
                level = min(1.0, rms / 5000)
                with self._wf_lock:
                    self._waveform.append(level)
                    if len(self._waveform) > 200:
                        self._waveform = self._waveform[-200:]
                # 分段計數：累積樣本數、連續靜音重置
                self._segment_samples += chunk_len
                if level < _SILENCE_LEVEL:
                    self._silence_chunks += 1
                else:
                    self._silence_chunks = 0

        try:
            _t0 = time.perf_counter()
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype='int16',
                callback=_callback,
            )
            _t1 = time.perf_counter()
            _safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] ⏱️ InputStream() {(_t1 - _t0) * 1000:.1f}ms')
            self._stream_start_time = time.perf_counter()
            self._stream.start()
            _t2 = time.perf_counter()
            _safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] ⏱️ stream.start() {(_t2 - _t1) * 1000:.1f}ms')
            _safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] 🎙️ 錄音就緒，總初始化 {(_t2 - _t0) * 1000:.1f}ms')
            return True
        except Exception as e:
            _safe_print(f'[recorder][start] ❌ 錄音裝置錯誤: {e}')
            self._recording = False
            return False

    def stop(self) -> bytes | None:
        """停止錄音並回傳 WAV bytes，若無音訊回傳 None。
        stream 保持開啟（預熱），待 shutdown() 才真正關閉。
        """
        with self._lock:
            if not self._recording:
                return None
            self._recording = False

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

    def shutdown(self) -> None:
        """實際關閉 stream（idle 超時或 app 退出時呼叫），釋放麥克風裝置。"""
        with self._lock:
            self._recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        _safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] 💤 預熱 stream 已關閉')

    def get_waveform(self) -> list[float]:
        """取得最近的波形資料（0~1 浮點陣列），供 UI 繪製"""
        with self._wf_lock:
            return self._waveform.copy()

    def get_accumulated_seconds(self) -> float:
        """回傳本段（上次 flush 後）已累積的錄音秒數"""
        return self._segment_samples / SAMPLE_RATE

    def get_silence_seconds(self) -> float:
        """回傳尾端連續靜音的秒數（依 RMS 判斷）"""
        chunk_sec = self._chunk_samples / SAMPLE_RATE if self._chunk_samples else 0.032
        return self._silence_chunks * chunk_sec

    def flush_segment(self) -> bytes | None:
        """取出並清空目前已累積的音訊（不停止錄音），回傳 WAV bytes；無語音則回傳 None"""
        with self._lock:
            if not self._recording or not self._frames:
                return None
            frames = self._frames
            self._frames = []
            self._segment_samples = 0
            # _silence_chunks 在 flush 完成後才重置，避免 flush 後立即重觸發

        audio_data = np.concatenate(frames, axis=0)
        duration = len(audio_data) / SAMPLE_RATE

        if duration < MIN_DURATION_SEC:
            _safe_print(f'[recorder][flush] 段落太短 ({duration:.2f}s)，略過')
            self._silence_chunks = 0
            return None

        if not _has_speech(audio_data):
            _safe_print('[recorder][flush] ❌ VAD 未偵測到語音，略過')
            self._silence_chunks = 0
            return None

        self._silence_chunks = 0
        _safe_print(f'[recorder][flush] ✅ 取出 {duration:.1f}s 音訊段落')
        return self._to_wav_bytes(audio_data)

    @property
    def is_recording(self) -> bool:
        return self._recording
