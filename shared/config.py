"""Runtime defaults shared by CLI, input sources and transport adapters."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
INTERFACE_FILE = PROJECT_DIR / "接口.json"
MODEL_DIR = PROJECT_DIR / "models" / "paraformer-large"
OUTPUT_DIR = PROJECT_DIR / "output"
DEFAULT_MODEL = "Ornith-1.5-35B-A3B-MLX-6bit"
DEFAULT_URL = "http://127.0.0.1:18000/v1/chat/completions"
DEFAULT_GCS_URL = "http://127.0.0.1:18001"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
# DeepSeek Key 通过环境变量 DEEPSEEK_API_KEY 注入；不要把 Key 写入此文件提交。
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")


@dataclass(frozen=True)
class AssistantConfig:
    text: str | None = None
    file: Path | None = None
    mic: bool = False
    deepseek: bool = False
    api_key: str | None = DEEPSEEK_API_KEY
    model: str = DEFAULT_MODEL
    url: str = DEFAULT_URL
    gcs_url: str = DEFAULT_GCS_URL
    asr_model_dir: Path = MODEL_DIR
    interface_file: Path = INTERFACE_FILE

    def __post_init__(self):
        if sum((self.text is not None, self.file is not None, self.mic)) > 1:
            raise ValueError("text、--file 和 --mic 只能选择一个")
        if self.deepseek:
            if self.model == DEFAULT_MODEL:
                object.__setattr__(self, "model", DEEPSEEK_MODEL)
            if self.url == DEFAULT_URL:
                object.__setattr__(self, "url", DEEPSEEK_URL)
