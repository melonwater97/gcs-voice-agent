"""Run a local Paraformer checkpoint on a file or interactive microphone input."""

import codecs
import os
import re
import select
import sys
import tempfile
from pathlib import Path

from shared.config import ASR_HOTWORDS_FILE, MODEL_DIR
from shared.reporting import log

# funasr 用 codecs.open(path, "r")（不传 encoding）读取热词文件，会回退到系统区域
# 编码：中文 Windows 上是 cp936/GBK，把本模块写出的 UTF-8 词表读成乱码并抛
# UnicodeDecodeError。这里在加载 funasr 前把「文本读取且未指定编码」的默认编码
# 固定为 UTF-8；显式编码和二进制读写保持原样，不受影响。
_codecs_open = codecs.open


def _utf8_codecs_open(filename, mode="r", encoding=None, errors="strict", buffering=-1):
    if encoding is None and mode in ("r", "rt"):
        encoding = "utf-8"
    return _codecs_open(filename, mode, encoding, errors, buffering)


codecs.open = _utf8_codecs_open


def load_hotwords(path: Path = ASR_HOTWORDS_FILE) -> list[str]:
    """Read one phrase per line; blank lines, comments and duplicates are ignored."""
    phrases = []
    for line in Path(path).expanduser().resolve().read_text(encoding="utf-8-sig").splitlines():
        phrase = line.partition("#")[0].strip()
        if phrase and phrase not in phrases:
            if any(char.isspace() for char in phrase):
                raise ValueError(f"热词不能包含空格，请每行填写一个词：{phrase}")
            phrases.append(phrase)
    return phrases


def _write_hotwords(directory: str, phrases) -> Path:
    # SeACo 的字符串入口会把 C型 当成一个未知词；文件入口允许同一行内按 token 分隔。
    path = Path(directory) / "hotwords.txt"
    lines = [" ".join(re.findall(r"[A-Za-z]+|[0-9]+|[^\s]", phrase))
             for phrase in dict.fromkeys(phrases)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_model(model_dir: Path = MODEL_DIR, hotwords_file: Path = ASR_HOTWORDS_FILE):
    """Load the shared local checkpoint once; importing this module loads no model."""
    model_dir = Path(model_dir).expanduser().resolve()
    if not model_dir.is_dir():
        raise ValueError(f"Model directory does not exist: {model_dir}")
    hotwords = load_hotwords(hotwords_file)
    if hotwords:
        import yaml

        config = yaml.safe_load((model_dir / "config.yaml").read_text(encoding="utf-8"))
        if config.get("model") not in {"SeacoParaformer", "ContextualParaformer"}:
            raise ValueError("当前模型不支持 hotword 热词，请使用 models/seaco-paraformer；"
                             "若需普通模型，请通过 --asr-hotwords-file 指定空词表")
    from funasr import AutoModel

    log("ASR", f"模型：{model_dir.name}；已加载 {len(hotwords)} 个热词：{hotwords_file}")
    directory = tempfile.TemporaryDirectory(prefix="gcs-hotwords-") if hotwords else None
    model = AutoModel(
        model=str(model_dir), device="cpu", disable_update=True, trust_remote_code=False,
        **({"hotword": str(_write_hotwords(directory.name, hotwords))} if directory else {}),
    )
    model.gcs_hotwords = tuple(hotwords)
    model.gcs_hotword_directory = directory  # 与模型同生命周期；销毁时清理临时词表
    return model


def transcribe_file(model, audio: Path, *, task_names: tuple[str, ...] = ()) -> str:
    """Return the transcript so callers can feed it into the assistant workflow."""
    audio = Path(audio).expanduser().resolve()
    if not audio.is_file():
        raise ValueError(f"Audio file does not exist: {audio}")
    base_words = getattr(model, "gcs_hotwords", ())
    if task_names and base_words:
        with tempfile.TemporaryDirectory(prefix="gcs-task-hotwords-") as directory:
            words = _write_hotwords(directory, (*base_words, *task_names))
            result = model.generate(input=str(audio), hotword=str(words))
    else:
        result = model.generate(input=str(audio))
    text = "".join(item.get("text", "") for item in result).strip()
    # SeACo 的时间戳分支返回按 token 分隔的文本；仅移除汉字旁的分隔空格，保留英文词间空格。
    return re.sub(r"(?<=[\u3400-\u9fff])\s+|\s+(?=[\u3400-\u9fff])", "", text)


def iter_microphone_transcripts(model, stop_event=None):
    """Yield each e/e recording's transcript; q or stop_event closes the session."""
    import termios
    import tty

    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    if not sys.stdin.isatty():
        raise SystemExit("--mic requires an interactive terminal")

    sample_rate = 16000
    terminal_fd = sys.stdin.fileno()
    original_settings = termios.tcgetattr(terminal_fd)
    stream = None
    chunks = []

    print("按 e 开始录音；再按 e 结束并识别；按 q 退出。无需按回车。", flush=True)
    try:
        tty.setcbreak(terminal_fd)
        while True:
            # Poll briefly so an async caller can stop the recording thread on Ctrl+C.
            if stop_event is not None and stop_event.is_set():
                break
            ready, _, _ = select.select([terminal_fd], [], [], 0.1)
            if not ready:
                continue
            key = os.read(terminal_fd, 1).decode("ascii", errors="ignore").lower()
            if not key:
                break
            if key == "q":
                print("已退出。", flush=True)
                break
            if key != "e":
                continue

            if stream is None:
                chunks = []

                def on_audio(indata, frames, time, status):
                    chunks.append(indata.copy())

                stream = sd.InputStream(
                    samplerate=sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=on_audio,
                )
                stream.start()
                print("录音中……按 e 结束，按 q 退出。", flush=True)
                continue

            current_stream = stream
            stream = None
            try:
                current_stream.stop()
            finally:
                current_stream.close()
            if not chunks:
                print("录音太短，请重试。", flush=True)
                continue

            samples = np.concatenate(chunks, axis=0)
            print("识别中……", flush=True)
            with tempfile.TemporaryDirectory(prefix="funasr-recording-") as directory:
                audio = Path(directory) / "microphone.wav"
                sf.write(audio, samples, sample_rate)
                transcript = transcribe_file(model, audio)
            yield transcript
            print("按 e 开始下一次录音，按 q 退出。", flush=True)
    except KeyboardInterrupt:
        print("\n已退出。", flush=True)
    finally:
        try:
            if stream is not None:
                try:
                    stream.stop()
                finally:
                    stream.close()
        finally:
            termios.tcsetattr(terminal_fd, termios.TCSADRAIN, original_settings)

