"""Persistent JSONL speech worker; stdout is reserved for protocol replies."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.environ.get("LH_SPEECH_ROOT", str(Path(__file__).resolve().parents[2])))
protocol = sys.stdout
recorder = None
model = None
model_key = None


def ensure_model(config):
    global model, model_key
    from inputs.speech import load_model
    from shared.config import MODEL_DIR, ASR_HOTWORDS_FILE
    key = (config.get("modelDir") or str(MODEL_DIR), config.get("hotwordsFile") or str(ASR_HOTWORDS_FILE))
    if model is None or key != model_key:
        model = load_model(Path(key[0]), Path(key[1]))
        model_key = key
    return {"ready": True, "modelDir": key[0], "hotwordsFile": key[1]}


def handle(command, config):
    global recorder, model, model_key
    if command == "probe":
        available = {name: importlib.util.find_spec(name) is not None for name in ["funasr", "sounddevice", "soundfile", "numpy"]}
        devices = []
        if available["sounddevice"]:
            import sounddevice as sd
            devices = [{"id": i, "name": d["name"]} for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
        from shared.config import MODEL_DIR, ASR_HOTWORDS_FILE
        model_dir = Path(config.get("modelDir") or MODEL_DIR)
        hotwords_file = Path(config.get("hotwordsFile") or ASR_HOTWORDS_FILE)
        return {"dependencies": available, "devices": devices,
                "modelDir": str(model_dir), "modelFilesPresent": all((model_dir / name).is_file() for name in ["model.pt", "config.yaml", "tokens.json"]),
                "hotwordsFile": str(hotwords_file), "hotwordsPresent": hotwords_file.is_file(), "asrReady": model is not None}
    if command == "warmup":
        return ensure_model(config)
    if command == "start":
        if recorder is not None:
            recorder.close()
        from inputs.recorder import create_recorder
        device = config.get("device")
        recorder = create_recorder(config.get("maxSeconds", 90), None if device in (None, "") else int(device))
        recorder.open()
        return recorder.snapshot()
    if command == "status":
        return recorder.snapshot() if recorder else {"listening": False, "level": 0, "seconds": 0, "max_seconds": config.get("maxSeconds", 90)}
    if command == "stop":
        if recorder is None:
            raise ValueError("没有进行中的录音")
        audio = recorder.finish()
        try:
            from inputs.speech import transcribe_file
            ensure_model(config)
            text = transcribe_file(model, audio)
            if not text.strip():
                raise ValueError("未识别到有效语音，请重新录音")
            return {"text": text}
        finally:
            audio.unlink(missing_ok=True)
            recorder.close()
            recorder = None
    raise ValueError("未知语音命令")


try:
    for line in sys.stdin:
        request = json.loads(line)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = handle(request["command"], request.get("config", {}))
            reply = {"id": request["id"], "result": result}
        except Exception as error:
            reply = {"id": request["id"], "error": str(error)}
        protocol.write(json.dumps(reply, ensure_ascii=False) + "\n")
        protocol.flush()
finally:
    if recorder:
        recorder.close()
