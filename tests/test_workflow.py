"""Verify task variants, three-stage MCP dispatch, and rejection without writes."""

import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from simulator.backend import TaskStore
from shared.config import PROJECT_DIR, AssistantConfig
from shared.contracts import PreLaunchTask, Message
from shared.messages import AGENT, MCP, make_message
from inputs.sources import input_texts
from assistant.workflow import run
from assistant.tools import DEFAULT_TOOLS
from shared.config import DEFAULT_MODEL, DEFAULT_URL


def pre_launch(mode: str, **parameters) -> dict:
    return {"action": "create_task", "task": {
        "task_type": "放飞前出任务", "task_name": "晨曦", "parameters": {"mode": mode, **parameters},
    }}


def c_formation(**overrides) -> dict:
    parameters = {"formation_type": "C型", "open_angle": 90, "radius": 100,
                  "position": {"longitude": 120.1, "latitude": 30.2}}
    return {**parameters, **overrides}


def u_formation(**overrides) -> dict:
    parameters = {"formation_type": "U型", "formation_length": 200, "formation_width": 80,
                  "position": {"longitude": 120.1, "latitude": 30.2}}
    return {**parameters, **overrides}


EXAMPLE = "新增放飞前出任务，原地起飞，起飞高度50米"
VALID = pre_launch("原地起飞", takeoff_altitude=50)
VARIANTS = [
    VALID,
    pre_launch("在指定位置展开", takeoff_altitude=50, formation=c_formation()),
    pre_launch("在指定位置展开", takeoff_altitude=80, formation=u_formation()),
]
INVALID_PAYLOADS = [
    pre_launch("原地起飞"),                                                   # 缺起飞高度
    pre_launch("原地起飞", takeoff_altitude=0),                               # 高度必须为正
    pre_launch("在指定位置展开", takeoff_altitude=50),                        # 缺编队参数
    pre_launch("在指定位置展开", takeoff_altitude=50,
               formation=c_formation(open_angle=0)),                          # 角度必须为正
    pre_launch("在指定位置展开", takeoff_altitude=50,
               formation=c_formation(position={"longitude": 181.0, "latitude": 30.0})),  # 经度越界
    pre_launch("在指定位置展开", takeoff_altitude=50,
               formation=u_formation(formation_width=0)),                     # 宽度必须为正
    pre_launch("在指定位置展开", takeoff_altitude=50,
               formation={"formation_type": "V型"}),                          # 未知编队类型
    pre_launch("在指定区域展开"),                                              # 未知模式
    {"action": "create_task", "task": {"task_type": "编队飞行任务", "parameters": {}}},  # 已并入放飞前出任务
    {"action": "create_task", "task": {"task_type": "未知任务", "parameters": {}}},
    {**VALID, "action": "start_flight"},                                      # 未知动作
]
PLACEHOLDER_ID = "TASK-" + "0" * 32


def message(payload: dict, name: str = "create_task") -> dict:
    return {"tool_calls": [{"function": {
        "name": name, "arguments": json.dumps({"payload": payload}, ensure_ascii=False),
    }}]}


def upload_call(task_id: str) -> dict:
    return {"tool_calls": [{"function": {
        "name": "upload_task", "arguments": json.dumps({"task_id": task_id}),
    }}]}


def validate_call(reply, allowed):
    invocation = DEFAULT_TOOLS.parse(reply, allowed)
    return invocation.arguments if invocation else None


def stub_local(reply: dict):
    """Replace LocalModel inside run(); every proposal returns the same reply."""

    class StubLocalModel:
        def __init__(self, model: str, url: str):
            self.reply = reply

        async def propose(self, messages: list[dict], tools: list[dict]) -> dict:
            return self.reply

    return StubLocalModel


@contextmanager
def openai_stub(reply: dict):
    """Minimal OpenAI-compatible chat endpoint so CLI tests avoid a real oMLX."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            body = json.dumps({"choices": [{"message": reply}]},
                              ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def workflow_args(directory: Path, **overrides) -> AssistantConfig:
    return AssistantConfig(**{
        "text": None, "file": None, "mic": False,
        "model": DEFAULT_MODEL, "url": DEFAULT_URL, "task_output_dir": Path(directory),
        "asr_model_dir": PROJECT_DIR / "models" / "paraformer-large",
        **overrides,
    })


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_dispatch_three_stages_and_refusal(self):
        with tempfile.TemporaryDirectory(prefix="gcs-layers-test-") as directory:
            params = StdioServerParameters(command=sys.executable, args=["-m", "integrations.mcp_server"], cwd=str(PROJECT_DIR),
                env={**os.environ, "TASK_OUTPUT_DIR": directory})
            async with Client(params) as client:
                tools = {tool.name for tool in (await client.list_tools()).tools}
                self.assertEqual(tools, {"create_task", "upload_task", "execute_task"})
                async def call(name, payload):
                    request = make_message(name.upper(), payload, src=AGENT, dst=MCP)
                    result = await client.call_tool(name, request)
                    if not result.is_error:
                        body = Message[dict].model_validate(result.structured_content)
                        self.assertEqual(body.header.src.model_dump(), MCP)
                        self.assertEqual(body.header.dst.model_dump(), AGENT)
                    return result

                self.assertTrue((await client.call_tool("create_task", {"payload": VALID})).is_error)
                bad_route = make_message("CREATE_TASK", VALID, src=AGENT, dst=AGENT)
                rejected = await client.call_tool("create_task", bad_route)
                self.assertEqual(rejected.structured_content["header"]["msg_type"], "ERROR")
                self.assertEqual(rejected.structured_content["header"]["src"], MCP)
                for payload in INVALID_PAYLOADS:
                    result = await call("create_task", payload)
                    self.assertTrue(result.is_error)
                self.assertTrue((await client.call_tool("start_flight", {})).is_error)
                self.assertEqual((await call("upload_task", {"task_id": "TASK-0"})).structured_content["header"]["msg_type"], "ERROR")
                self.assertEqual(list(Path(directory).iterdir()), [])
                task_ids = set()
                for payload in VARIANTS:
                    with self.subTest(payload=payload):
                        command = validate_call(message(payload), tools)
                        self.assertEqual(command.payload.model_dump(), payload)
                        created = await call("create_task", command.payload.model_dump())
                        self.assertFalse(created.is_error)
                        draft = created.structured_content["payload"]
                        self.assertEqual(draft["status"], "draft")
                        self.assertEqual(draft["task"], payload["task"])
                        task_id = draft["task_id"]
                        # 未上传不能执行
                        self.assertEqual((await call(
                            "execute_task", {"task_id": task_id})).structured_content["header"]["msg_type"], "ERROR")
                        uploaded = await call("upload_task", {"task_id": task_id})
                        self.assertFalse(uploaded.is_error)
                        result = uploaded.structured_content["payload"]
                        self.assertEqual(result["status"], "uploaded")
                        self.assertEqual(result["task"], payload["task"])
                        self.assertEqual(json.loads(Path(result["file"]).read_text()),
                                         result)
                        executed = await call("execute_task", {"task_id": task_id})
                        self.assertEqual(executed.structured_content["payload"],
                                         {"task_id": task_id, "status": "executing"})
                        task_ids.add(task_id)
                self.assertEqual(len(task_ids), 3)
                self.assertEqual(len(list(Path(directory).glob("*.json"))), 3)

    def test_l2_rejects_unknown_tools_and_invalid_payloads(self):
        allowed = {"create_task", "upload_task", "execute_task"}
        with self.assertRaises(ValueError):
            validate_call(message(VALID, "start_flight"), allowed)
        for payload in INVALID_PAYLOADS:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_call(message(payload), allowed)
        with self.assertRaises(ValueError):
            validate_call(upload_call("BAD-ID"), allowed)
        self.assertIsNone(validate_call({"content": "需要澄清"}, allowed))

    def test_l2_rejects_extra_arguments_and_multiple_calls(self):
        invalid = message(VALID)
        invalid["tool_calls"][0]["function"]["arguments"] = json.dumps(
            {"payload": VALID, "header": {"msg_type": "START_FLIGHT"}})
        with self.assertRaises(ValueError):
            validate_call(invalid, {"create_task"})
        duplicate = message(VALID)
        duplicate["tool_calls"] *= 2
        with self.assertRaises(ValueError):
            validate_call(duplicate, {"create_task"})

    def test_task_store_rejects_invalid_input_and_transitions_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory))
            for payload in (p for p in INVALID_PAYLOADS if p["action"] == "create_task"):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    store.create(payload["task"])
            task_id = store.create(PreLaunchTask.model_validate(VALID["task"]))
            with self.assertRaisesRegex(ValueError, "不能转到"):
                store.execute(task_id)
            with self.assertRaisesRegex(ValueError, "不存在"):
                store.upload("TASK-unknown")
            self.assertEqual(list(Path(directory).iterdir()), [])
            uploaded = store.upload(task_id)
            self.assertEqual(json.loads(Path(uploaded["file"]).read_text()), uploaded)
            with self.assertRaisesRegex(ValueError, "不能转到"):
                store.upload(task_id)
            store.execute(task_id)
            with self.assertRaisesRegex(ValueError, "不能转到"):
                store.execute(task_id)

    async def test_asr_file_transcript_flows_through_mcp(self):
        with tempfile.TemporaryDirectory(prefix="gcs-asr-file-test-") as directory:
            args = workflow_args(Path(directory), file=Path("recording.wav"))
            asr_model = object()
            with patch("assistant.workflow.LocalModel", stub_local(message(VARIANTS[0]))), \
                 patch("inputs.speech.load_model", return_value=asr_model) as load, \
                 patch("inputs.speech.transcribe_file", return_value="新增放飞前出任务原地起飞起飞高度50米") as transcribe, \
                 redirect_stdout(StringIO()) as output:
                await run(args)
            load.assert_called_once_with(args.asr_model_dir, args.asr_hotwords_file)
            transcribe.assert_called_once_with(asr_model, args.file)
            self.assertIn("已创建放飞前出任务", output.getvalue())
            self.assertIn("TASK-", output.getvalue())
            # 阶段一仅在助手侧创建草稿，地面站不产生文件
            self.assertEqual(list(Path(directory).glob("TASK-*.json")), [])

    async def test_microphone_session_reuses_asr_for_multiple_commands(self):
        with tempfile.TemporaryDirectory(prefix="gcs-asr-mic-test-") as directory:
            args = workflow_args(Path(directory), mic=True)
            with patch("assistant.workflow.LocalModel", stub_local(message(VARIANTS[2]))), \
                 patch("inputs.speech.load_model", return_value=object()) as load, \
                 patch("inputs.speech.iter_microphone_transcripts", return_value=(text for text in [
                     "", EXAMPLE, "新增在指定位置展开任务，U型编队，起飞高度80米"])) as recordings, \
                 redirect_stdout(StringIO()) as output:
                await run(args)
            load.assert_called_once()
            recordings.assert_called_once()
            self.assertEqual(output.getvalue().count("已创建放飞前出任务"), 2)
            self.assertEqual(list(Path(directory).glob("TASK-*.json")), [])

    def test_failed_save_keeps_draft_and_existing_files_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / "blocked"
            blocked.write_text("existing file")
            store = TaskStore(blocked)
            task_id = store.create(VALID["task"])
            with self.assertRaises(OSError):
                store.upload(task_id)
            self.assertEqual(store.entry(task_id)["status"], "draft")
            self.assertEqual(blocked.read_text(), "existing file")
            store.output_dir = Path(directory)
            destination = Path(directory) / f"{task_id}.json"
            destination.write_text("existing task")
            with self.assertRaisesRegex(ValueError, "未覆盖"):
                store.upload(task_id)
            self.assertEqual(destination.read_text(), "existing task")
            self.assertEqual(store.entry(task_id)["status"], "draft")
            destination.unlink()
            store.upload(task_id)
            self.assertEqual(store.entry(task_id)["status"], "uploaded")

    async def test_cancelled_microphone_wait_stops_recorder(self):
        ready = threading.Event()
        closed = threading.Event()

        def recording(model, stop_event):
            try:
                ready.set()
                while not stop_event.wait(0.01):
                    pass
                return
                yield ""  # Keep the fixture a generator, like the actual recorder.
            finally:
                closed.set()

        with patch("inputs.speech.load_model", return_value=object()), \
             patch("inputs.speech.iter_microphone_transcripts", side_effect=recording), \
             redirect_stdout(StringIO()):
            inputs = input_texts(workflow_args(Path(tempfile.gettempdir()), mic=True))
            pending = asyncio.create_task(anext(inputs))
            self.assertTrue(await asyncio.to_thread(ready.wait, 2))
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            await inputs.aclose()
        self.assertTrue(closed.is_set())


if __name__ == "__main__":
    unittest.main()
