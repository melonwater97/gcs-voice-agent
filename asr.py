"""Run a local Paraformer checkpoint on a file or interactive microphone input."""

import argparse
import os
import select
import sys
import tempfile
import termios
import tty
from pathlib import Path


MODEL_DIR = Path(__file__).resolve().parent / "models" / "paraformer-large"


def load_model(model_dir: Path = MODEL_DIR):
    """Load the shared local checkpoint once; importing this module loads no model."""
    model_dir = Path(model_dir).expanduser().resolve()
    if not model_dir.is_dir():
        raise ValueError(f"Model directory does not exist: {model_dir}")
    from funasr import AutoModel

    return AutoModel(
        model=str(model_dir), device="cpu", disable_update=True, trust_remote_code=False,
    )


def transcribe_file(model, audio: Path) -> str:
    """Return the transcript so callers can feed it into the assistant workflow."""
    audio = Path(audio).expanduser().resolve()
    if not audio.is_file():
        raise ValueError(f"Audio file does not exist: {audio}")
    result = model.generate(input=str(audio))
    return "".join(item.get("text", "") for item in result).strip()


def iter_microphone_transcripts(model, stop_event=None):
    """Yield each e/e recording's transcript; q or stop_event closes the session."""
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


def transcribe_microphone(model) -> None:
    for transcript in iter_microphone_transcripts(model):
        print(transcript, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Chinese speech recognition")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Local audio file")
    source.add_argument("--mic", action="store_true", help="Control the default microphone with e/e/q")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    args = parser.parse_args()
    if args.mic and not sys.stdin.isatty():
        parser.error("--mic requires an interactive terminal")

    model_dir = args.model_dir.expanduser().resolve()
    if not model_dir.is_dir():
        parser.error(f"Model directory does not exist: {model_dir}")

    if args.file is not None and not args.file.expanduser().resolve().is_file():
        parser.error(f"Audio file does not exist: {args.file}")
    model = load_model(model_dir)

    if args.file is not None:
        print(transcribe_file(model, args.file))
    else:
        transcribe_microphone(model)


if __name__ == "__main__":
    main()
