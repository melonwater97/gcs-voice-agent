"""服务端麦克风录音器：供 Agent 录音端点使用，不依赖终端。

与 `speech.iter_microphone_transcripts` 的终端按键采集不同，本模块由 HTTP 请求
显式 start/stop，底层把阻塞的 sounddevice 开关麦交给调用方放到工作线程执行。
"""

import tempfile
import threading
from pathlib import Path

import numpy as np

from shared.reporting import log

MIN_SECONDS = 0.25


class MicRecorder:
    """16000Hz 单声道 float32 录音；回调在 PortAudio 线程写状态，snapshot/finish 读。"""

    def __init__(self, max_seconds: int = 90, sample_rate: int = 16000, device: int | None = None):
        self.max_seconds = max_seconds
        self.sample_rate = sample_rate
        self.device = device
        self._lock = threading.Lock()
        self._stream = None
        self._timer = None
        self._chunks: list = []
        self._frames = 0
        self._level = 0.0
        self._listening = False
        self._closed = False

    def open(self) -> dict:
        """打开麦克风；已在监听时幂等返回当前快照。设备不可用或已关闭抛 ValueError。"""
        import sounddevice as sd

        with self._lock:
            if self._closed:
                raise ValueError("录音器已关闭")
            if self._listening:
                return self._snapshot()
            self._chunks = []
            self._frames = 0
            self._level = 0.0
        try:
            # 回调在独立音频线程执行；start() 仅在打开设备时短暂阻塞。
            stream = sd.InputStream(samplerate=self.sample_rate, channels=1,
                                    dtype="float32", device=self.device, callback=self._on_audio)
            stream.start()
        except Exception as exc:
            raise ValueError(f"无法打开麦克风：{exc}") from exc
        # 一次性定时器：到点后从独立线程释放麦克风，避免无人轮询时长时间占用设备。
        timer = threading.Timer(self.max_seconds, self._auto_stop)
        timer.daemon = True
        with self._lock:
            if self._closed:  # 与 close() 竞态：放弃刚打开的流，不残留设备
                timer.cancel()
                _close_stream(stream)
                raise ValueError("录音器已关闭")
            self._stream = stream
            self._listening = True
            self._timer = timer
        timer.start()
        log("录音", f"麦克风已开启，单次最长 {self.max_seconds} 秒")
        return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot()

    def finish(self) -> Path:
        """停止采集并写出临时 WAV；无音频或过短时抛 ValueError。"""
        import soundfile as sf

        with self._lock:
            stream, self._stream = self._stream, None
            timer, self._timer = self._timer, None
            self._listening = False
            chunks, self._chunks = self._chunks, []
        if timer is not None:
            timer.cancel()
        _close_stream(stream)

        length = sum(len(chunk) for chunk in chunks)
        if length < self.sample_rate * MIN_SECONDS:
            raise ValueError("录音太短，请说完口令后再结束")
        samples = np.concatenate(chunks, axis=0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as file:
            path = Path(file.name)
        sf.write(path, samples, self.sample_rate)
        log("录音", f"录音结束，时长 {length / self.sample_rate:.1f} 秒")
        return path

    def close(self) -> None:
        with self._lock:
            self._closed = True
            stream, self._stream = self._stream, None
            timer, self._timer = self._timer, None
            self._listening = False
            self._chunks = []
        if timer is not None:
            timer.cancel()
        _close_stream(stream)

    # ---- 内部 ----

    def _snapshot(self) -> dict:
        return {"listening": self._listening, "level": round(self._level, 3),
                "seconds": round(self._frames / self.sample_rate, 1),
                "max_seconds": self.max_seconds}

    def _on_audio(self, indata, frames, time_info, status) -> None:
        samples = indata[:, 0].copy()
        with self._lock:
            if not self._listening:
                return
            self._chunks.append(samples)
            self._frames += len(samples)
            rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
            self._level = min(1.0, rms * 7)

    def _auto_stop(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
            was_listening = self._listening
            self._listening = False
        if was_listening:
            log("录音", f"达到 {self.max_seconds} 秒上限，自动停止监听")
            _close_stream(stream)


def _close_stream(stream) -> None:
    if stream is None:
        return
    try:
        stream.stop()
    except Exception as exc:  # 释放尽力而为，不覆盖上层错误
        print(f"[录音] 停止音频流异常：{exc}", flush=True)
    try:
        stream.close()
    except Exception as exc:
        print(f"[录音] 关闭音频流异常：{exc}", flush=True)


def create_recorder(max_seconds: int = 90, device: int | None = None) -> MicRecorder:
    """录音器工厂：Agent 与测试用它注入不同实现。"""
    return MicRecorder(max_seconds=max_seconds, device=device)
