"""Browser test fixture: direct browser → Agent → MCP, deterministic ASR/LLM/录音 only.

Run from the repository root: python -m tests.web_test_server
Uses temporary storage and isolated ports; never writes production task files.
"""
import json
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent.http_server import build_runtime
from shared.config import AssistantConfig
from tests.fakes import FakeRecorderFactory


class BrowserTestModel:
    def __init__(self, *args):
        pass

    async def propose(self, messages, tools):
        text = messages[-1]["content"]
        if text == "新增放飞前出任务，原地起飞，起飞高度50米":
            await asyncio.sleep(1.5)  # 给浏览器验证转写先显示、结果后到达的时间窗口。
        if "缺参" in text:
            return {"content": "请补充起飞高度（单位：米）。"}
        if "上传" in text or "执行" in text:
            results = [json.loads(item["content"]) for item in messages if item.get("role") == "tool"]
            arguments = {"task_id": results[-1]["task_id"]}
            name = "execute_task" if "执行" in text else "upload_task"
        else:
            name = "create_task"
            arguments = {"payload": {"action": name, "task": {
                "task_type": "放飞前出任务", "parameters": {"mode": "原地起飞", "takeoff_altitude": 50}}}}
        return {"tool_calls": [{"function": {"name": name, "arguments": json.dumps(arguments)}}]}


def main():
    with tempfile.TemporaryDirectory(prefix="gcs-browser-") as directory:
        config = AssistantConfig(task_output_dir=Path(directory))
        with patch("agent.http_server.LocalModel", BrowserTestModel), \
                patch("inputs.speech.transcribe_file", return_value="新增放飞前出任务，原地起飞，起飞高度50米"):
            runtime = build_runtime(config, "127.0.0.1", 18012, asr_model=object(),
                                    recorder_factory=FakeRecorderFactory())
            try:
                runtime.server.serve_forever()
            finally:
                runtime.close()


if __name__ == "__main__":
    main()
