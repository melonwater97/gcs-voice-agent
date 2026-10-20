"""Deterministic recording fakes: tests never touch a real microphone."""

import io
import tempfile
import wave
from pathlib import Path


def wav_bytes(frames: int = 1600) -> bytes:
    """构造 16000Hz 单声道 16bit WAV 字节，模拟服务端录音产物。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


def write_wav_file(frames: int = 1600) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as file:
        file.write(wav_bytes(frames))
        return Path(file.name)


class FakeRecorder:
    """替身录音器：不进真实麦克风，finish 返回固定的 PCM16 WAV。"""

    def __init__(self, max_seconds: int = 90):
        self.max_seconds = max_seconds
        self.listening = False
        self.level = 0.0
        self.seconds = 0.0
        self.fail_open = False
        self.opened = 0
        self.finished = 0
        self.closed = 0

    def open(self) -> dict:
        if self.fail_open:
            raise ValueError("麦克风设备被占用")
        self.opened += 1
        self.listening = True
        return self.snapshot()

    def snapshot(self) -> dict:
        return {"listening": self.listening, "level": self.level,
                "seconds": self.seconds, "max_seconds": self.max_seconds}

    def finish(self) -> Path:
        self.finished += 1
        self.listening = False
        return write_wav_file()

    def close(self) -> None:
        self.closed += 1
        self.listening = False


class FakeRecorderFactory:
    """按 AgentService 的工厂约定创建 FakeRecorder，并保留实例供断言。"""

    def __init__(self, fail_open: bool = False):
        self.fail_open = fail_open
        self.instances: list[FakeRecorder] = []

    def __call__(self, max_seconds: int) -> FakeRecorder:
        recorder = FakeRecorder(max_seconds)
        recorder.fail_open = self.fail_open
        self.instances.append(recorder)
        return recorder
